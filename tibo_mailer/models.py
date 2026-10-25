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
    quoted_post: Post | None = None

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
                "context_text": self.context_text, "truncated": self.truncated,
                "quoted_post": self.quoted_post.public_dict() if self.quoted_post else None}

    def evidence_dict(self):
        return self.public_dict()

    @classmethod
    def from_dict(cls, value):
        quoted = value.get("quoted_post")
        return cls(value["id"], value["text"], datetime.fromisoformat(value["created_at"]),
                   value.get("author", ACCOUNT), value.get("parent_id"), value.get("context_text", ""),
                   value.get("truncated", False), cls.from_dict(quoted) if quoted else None)


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
        # New greetings/reactions in a thread do not announce a new reset.
        return hashlib.sha256((self.status + "\n" + self.timing_post.version + "\n" + self.timing_quote).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)
