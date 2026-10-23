from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

ACCOUNT = "thsottiaux"


@dataclass(frozen=True)
class Post:
    id: str
    text: str
    created_at: datetime
    author: str = ACCOUNT
    parent_id: str | None = None
    context_text: str = ""
    truncated: bool = False

    @property
    def url(self):
        return f"https://x.com/{ACCOUNT}/status/{self.id}"

    @property
    def version(self):
        return self.id + ":" + hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def root_id(self):
        return self.parent_id or self.id

    def public_dict(self):
        return {"id": self.id, "text": self.text, "created_at": self.created_at.isoformat(),
                "url": self.url, "author": self.author, "parent_id": self.parent_id,
                "context_text": self.context_text, "truncated": self.truncated}


@dataclass
class FetchResult:
    posts: list[Post]
    warnings: list[str]
    checked_at: datetime
    complete_history: bool = False


@dataclass
class Event:
    posts: list[Post]
    status: str
    summary: str
    timing_quote: str
    timing_post: Post

    @property
    def versions(self):
        return sorted({p.version for p in self.posts})

    @property
    def key(self):
        return hashlib.sha256("\n".join(self.versions).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)
