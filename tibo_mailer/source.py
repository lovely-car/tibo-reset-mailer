"""Read the account's official public HTML. No search cache or proxy fallback."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

from .models import ACCOUNT, FetchResult, Post, utcnow

PROFILE = f"https://x.com/{ACCOUNT}"
STATUS = re.compile(rf"^/{ACCOUNT}/status/(\d+)$", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def walk(self, skip_articles=False):
        for child in self.children:
            if isinstance(child, Node):
                if skip_articles and child.tag == "article":
                    continue
                yield child
                yield from child.walk(skip_articles)

    def text(self, raw=False):
        if not raw and self.tag in {"script", "style", "svg", "article"}:
            return ""
        if not raw and self.tag == "img":
            return self.attrs.get("alt", "")
        if self.tag == "br":
            return "\n"
        return "".join(c.text(raw) if isinstance(c, Node) else c for c in self.children)


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node("root")
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_posts(html: str, page_url: str) -> list[Post]:
    root = Tree(html).root

    def parse_article(article):
        ancestor = article.parent
        nodes = list(article.walk(skip_articles=True))
        authors = [n.attrs.get("href", "") for n in nodes if n.tag == "a"
                   and re.fullmatch(r"/[A-Za-z0-9_]+", n.attrs.get("href", ""))]
        if not authors or authors[0].lower() != f"/{ACCOUNT}":
            return None
        if any(n.attrs.get("data-testid") == "socialContext" and
               re.search(r"repost|retweet|转帖|转推", n.text(), re.I) for n in nodes):
            return None
        path = None
        for node in ancestors(ancestor):
            if node.tag == "article":
                break
            if STATUS.fullmatch(node.attrs.get("data-href", "")):
                path = node.attrs["data-href"]
                break
        links = [n for n in nodes if n.tag == "a" and STATUS.fullmatch(n.attrs.get("href", ""))]
        body = next((n for n in nodes if n.attrs.get("data-testid") == "tweetText"), None)
        if body is None:
            body = next((n for n in nodes if n.tag == "div" and n.attrs.get("dir") == "auto"), None)
        if body is None or not body.text().strip():
            return None
        dated_links = []
        for link in links:
            created = None
            for time in (n for n in link.walk() if n.tag == "time"):
                try:
                    dt = datetime.fromisoformat(time.attrs.get("datetime", "").replace("Z", "+00:00"))
                    if dt.tzinfo:
                        created = dt.astimezone(timezone.utc)
                except ValueError:
                    pass
            if not created and link.attrs.get("id"):
                for script in (n for n in nodes if n.tag == "script"):
                    raw = script.text(raw=True)
                    if json.dumps(link.attrs["id"]) not in raw:
                        continue
                    match = re.search(r'"timestamp"\s*:\s*(\d{10,16})', raw)
                    if match:
                        created = datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc)
                        break
            if created:
                dated_links.append((link.attrs["href"], created))
        path = path or (dated_links[0][0] if dated_links else None)
        created = next((dt for href, dt in dated_links if href == path), None)
        if not path or not created:
            return None
        post_id = STATUS.fullmatch(path)[1]
        quoted = next((n for n in article.walk() if n.tag == "article"), None)
        quoted_post = parse_article(quoted) if quoted else None
        post = Post(post_id, body.text().strip(), created, truncated=any(
            n.tag in {"button", "a"} and n.text().strip().lower() in {"show more", "显示更多"}
            for n in nodes), quoted_post=quoted_post)
        return post

    posts = {}
    for article in (n for n in root.walk() if n.tag == "article"):
        if any(n.tag == "article" for n in ancestors(article.parent)):
            continue
        post = parse_article(article)
        if post is None:
            continue
        post_id = post.id
        if post_id not in posts or len(post.text) > len(posts[post_id].text):
            posts[post_id] = post
    match = re.fullmatch(rf"{re.escape(PROFILE)}/status/(\d+)", page_url)
    if match and match[1] in posts:
        parent = posts[match[1]]
        # Same-thread context is evidence to review, not a verified direct reply edge.
        from dataclasses import replace
        posts = {k: replace(p, parent_id=parent.id, context_text=parent.text)
                 if k != parent.id else p for k, p in posts.items()}
    return sorted(posts.values(), key=lambda p: p.created_at, reverse=True)


def ancestors(node):
    while node:
        yield node
        node = node.parent


def fetch_html(url: str):
    if not re.fullmatch(rf"{re.escape(PROFILE)}(?:/status/\d+)?", url):
        raise ValueError("Only the official account and its permalinks are allowed")
    request = Request(url, headers={"User-Agent": "tibo-reset-mailer/0.1 (+public-source-check)",
                                    "Accept": "text/html", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=20) as response:
        if response.status != 200 or not re.fullmatch(rf"{re.escape(PROFILE)}(?:/status/\d+)?", response.geturl()):
            raise RuntimeError("Unexpected X response or redirect")
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise RuntimeError("X response exceeded the size limit")
        return body.decode("utf-8")


def fetch_posts(now=None, retry_urls=()) -> FetchResult:
    now = now or utcnow()
    profile = parse_posts(fetch_html(PROFILE), PROFILE)
    if not profile:
        raise RuntimeError("X returned no verifiable author/text/timestamp; this is a source failure")
    posts = {p.id: p for p in profile}
    relevant = [p for p in profile if p.truncated or p.quoted_post or re.search(
        r"reset|quota|usage|allowance|limit|capacity|propagat|codexing", p.text, re.I)][:4]
    targets = {p.url: p.id for p in relevant}
    for url in list(dict.fromkeys(retry_urls))[:4]:
        if re.fullmatch(rf"{re.escape(PROFILE)}/status/\d+", url):
            targets.setdefault(url, url.rsplit("/", 1)[1])
    warnings, unavailable = [], set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_html, url): (url, pid) for url, pid in targets.items()}
        for future in as_completed(futures):
            url, parent_id = futures[future]
            try:
                thread = parse_posts(future.result(), url)
                if not any(p.id == parent_id for p in thread):
                    raise RuntimeError("Original post missing from its thread")
                for post in thread:
                    if post.id not in posts or len(post.text) >= len(posts[post.id].text):
                        posts[post.id] = post
            except Exception:
                warnings.append(f"Thread {parent_id} unavailable; its evidence will be retried")
                unavailable.add(parent_id)
    cutoff = now - timedelta(days=7)
    recent = [p for p in posts.values() if cutoff <= p.created_at <= now + timedelta(minutes=5)
              and p.root_id not in unavailable and p.id not in unavailable and not p.truncated]
    return FetchResult(sorted(recent, key=lambda p: p.created_at, reverse=True)[:100], warnings, now)
