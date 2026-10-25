"""Durable semantic review; uncertainty is separate from delivery and seen state."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from dataclasses import replace

from .analysis import COMPLETE, RESET, codex_context, grouped, inventory, series_anchors
from .models import ACCOUNT, Post, utcnow

CATEGORIES = {"other-product", "api-rate-limit", "billing-credit", "not-a-reset", "unrelated"}


def suspect(post):
    text = post.text + "\n" + post.context_text
    return bool(post.truncated or post.quoted_post or RESET.search(text) or COMPLETE.search(text)
                or re.search(r"quota|allowance|usage|limit|capacity|propagat|codexing|重置|额度", text, re.I))


def remember(state, posts):
    with state.db:
        for post in inventory(posts).values():
            previous = state.db.execute("SELECT post FROM observations WHERE post_id=?", (post.id,)).fetchone()
            if previous and not post.parent_id:
                old = Post.from_dict(json.loads(previous[0]))
                if old.version == post.version and old.parent_id:
                    # A compact own-quote card must not erase a previously
                    # verified thread edge just because the root aged off profile.
                    post = replace(post, parent_id=old.parent_id, context_text=old.context_text)
            state.db.execute("INSERT OR REPLACE INTO observations VALUES (?,?)",
                             (post.id, json.dumps(post.public_dict(), ensure_ascii=False)))


def history(state, now):
    posts = [p for row in state.db.execute("SELECT post FROM observations")
             if now - timedelta(days=7) <= (p := Post.from_dict(json.loads(row[0]))).created_at <= now + timedelta(minutes=5)]
    by_id = {p.id: p for p in posts}
    return [replace(p, context_text=by_id[p.parent_id].text) if p.parent_id in by_id else p for p in posts]


def retry_urls(state, now=None):
    now = now or utcnow()
    urls = []
    for row in state.db.execute("SELECT post FROM reviews WHERE status='deferred' ORDER BY at"):
        post = Post.from_dict(json.loads(row[0]))
        if post.created_at >= now - timedelta(days=7):
            url = f"https://x.com/{ACCOUNT}/status/{post.parent_id}" if post.parent_id else post.url
            if url not in urls:
                urls.append(url)
    return urls[:4]


def decision(state, post, fingerprint, status, reason):
    now = utcnow().isoformat()
    with state.db:
        state.db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?)",
                         (post.version, fingerprint, status, reason, json.dumps(post.public_dict(), ensure_ascii=False), now))
        state.db.execute("INSERT INTO review_audit(version,status,reason,at) VALUES (?,?,?,?)", (post.version, status, reason, now))


def collect(state, result):
    # History supplies context only. A candidate enters this scan only if it
    # was verified in the current fetch; an offline cached row cannot trigger mail.
    remember(state, result.posts)
    pool = history(state, result.checked_at)
    pending_ids = {Post.from_dict(json.loads(row[0])).id for row in state.db.execute("SELECT post FROM reviews WHERE status='deferred'")}
    selected = result.posts[:20] if state.initialized else result.posts
    selected_ids = {p.id for p in selected}
    selected += [p for p in result.posts if p.id in pending_ids and p.id not in selected_ids]
    groups = grouped(pool)
    by_id = inventory(pool)
    from .analysis import group_key
    fresh, reasons, fingerprints, roots = [], {}, {}, set()
    covered = {v for d in state.deliveries({"pending", "unknown", "sent"}) for v in d["versions"]}
    for post in selected:
        group = groups.get(group_key(post, by_id), [post])
        anchors = series_anchors(group, pool)
        evidence = sorted({p.id: p.public_dict() for p in group + anchors}.values(), key=lambda p: p["id"])
        fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row = state.db.execute("SELECT * FROM reviews WHERE version=?", (post.version,)).fetchone()
        changed = row is not None and row["fingerprint"] != fingerprint
        deferred = row is not None and row["status"] == "deferred"
        legacy = row is None and state.seen(post.version) and suspect(post) and post.version not in covered
        if not state.seen(post.version) or changed or deferred or legacy:
            reason = "context-changed" if changed else "deferred-recheck" if deferred else "legacy-recheck" if legacy else "new-version"
            fresh.append(post)
            reasons[post.version] = reason
            fingerprints[post.version] = fingerprint
            roots.add(group_key(post, by_id))
            if suspect(post) and (not deferred or changed):
                decision(state, post, fingerprint, "deferred", reason)
        elif row is None:
            decision(state, post, fingerprint, "processed", "Legacy seen baseline; prior delivery/history preserved")
    evidence = {p.id: p for root in roots for p in groups.get(root, [])}
    for post in fresh:
        evidence[post.id] = post
    for anchor in pool:
        if codex_context(anchor.text):
            evidence.setdefault(anchor.id, anchor)
    return fresh, list(evidence.values()), reasons, fingerprints


def outcomes(batch, matched=(), review=None):
    review = review or {}
    deferred, excluded = {}, {}
    by_id = {p["id"]: Post.from_dict(p) for p in batch["posts"]}
    fresh_ids = {p.id for p in by_id.values() if p.version in batch["new_versions"]}
    for key, target in (("deferred", deferred), ("excluded", excluded)):
        for item in review.get(key, []):
            if not isinstance(item, dict) or item.get("post_id") not in fresh_ids or not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError("Review decisions require a current post_id and specific reason")
            if key == "excluded" and item.get("category") not in CATEGORIES:
                raise ValueError("Exclusions require a concrete category; uncertainty must stay deferred")
            target[item["post_id"]] = item["reason"]
    if deferred.keys() & excluded.keys():
        raise ValueError("A post cannot be both excluded and deferred")
    result = []
    for post in by_id.values():
        if post.version not in batch["new_versions"]:
            continue
        if post.version in matched:
            result.append((post, "reserved", "Included in a validated reset event"))
        elif post.id in excluded or not suspect(post):
            result.append((post, "irrelevant", excluded.get(post.id, "No reset or timing candidate evidence")))
        else:
            result.append((post, "deferred", deferred.get(post.id, "Reset candidate lacks a validated event; recheck own context")))
    return result


def finish(state, batch, results):
    for post, status, reason in results:
        decision(state, post, batch["review_fingerprints"][post.version], status, reason)
    state.mark_seen([post.version for post, status, _ in results if status != "deferred"])
    with state.db:
        state.db.execute("INSERT INTO processed_scans VALUES (?)", (batch["scan_id"],))
    return sum(status == "deferred" for _, status, _ in results)
