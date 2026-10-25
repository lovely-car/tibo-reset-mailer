from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import utcnow


@contextmanager
def process_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "monitor.lock"
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Another mailer process is using this data directory") from None
    try:
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        database = directory / "state.sqlite3"
        self.db = sqlite3.connect(database, timeout=10)
        if os.name != "nt":
            database.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS seen (version TEXT PRIMARY KEY, at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS deliveries (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
            versions TEXT NOT NULL, onboarding INTEGER NOT NULL, at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS receipts (delivery_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations (post_id TEXT PRIMARY KEY, post TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reviews (
            version TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, post TEXT NOT NULL, at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS review_audit (
            id INTEGER PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS processed_scans (id TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS event_claims (event_key TEXT PRIMARY KEY, delivery_id TEXT NOT NULL);
        """)
        # A crash during SMTP can leave the outcome ambiguous. Never auto-resend.
        with self.db:
            self.db.execute("UPDATE deliveries SET status='unknown', error='Process ended during SMTP; verify sent mail before resolving' WHERE status='sending'")

    def close(self):
        self.db.close()

    @property
    def initialized(self):
        return bool(self.db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone())

    def seen(self, version):
        return bool(self.db.execute("SELECT 1 FROM seen WHERE version=?", (version,)).fetchone())

    def mark_seen(self, versions):
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(v, utcnow().isoformat()) for v in versions])

    def reserve(self, delivery_id, payload, versions, onboarding=False, event_keys=()):
        with self.db:
            if any(self.db.execute("SELECT 1 FROM event_claims WHERE event_key=?", (key,)).fetchone() for key in event_keys):
                return False
            cursor = self.db.execute("INSERT OR IGNORE INTO deliveries(id,status,payload,versions,onboarding,at) VALUES (?,'pending',?,?,?,?)",
                                     (delivery_id, json.dumps(payload, ensure_ascii=False), json.dumps(versions), int(onboarding), utcnow().isoformat()))
            self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(v, utcnow().isoformat()) for v in versions])
            if cursor.rowcount == 1:
                self.db.executemany("INSERT INTO event_claims VALUES (?,?)", [(key, delivery_id) for key in event_keys])
        return cursor.rowcount == 1

    def event_claimed(self, event):
        if self.db.execute("SELECT 1 FROM event_claims WHERE event_key=?", (event.key,)).fetchone():
            return True
        # Upgrade compatibility: legacy deliveries had no event key. Only an
        # actual quoted schedule/completion in that mail substantiates a match.
        return any(event.timing_post.version in d["versions"] and event.timing_quote in d["payload"].get("text", "")
                   for d in self.deliveries({"pending", "unknown", "sent"}))

    def deliveries(self, statuses=None):
        rows = self.db.execute("SELECT * FROM deliveries ORDER BY at").fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"]), "versions": json.loads(r["versions"])}
                for r in rows if statuses is None or r["status"] in statuses]

    def update(self, delivery_id, status, error=""):
        with self.db:
            cursor = self.db.execute("UPDATE deliveries SET status=?,error=?,attempts=attempts+? WHERE id=?",
                                    (status, error, int(status == "sending"), delivery_id))
            if cursor.rowcount != 1:
                raise ValueError("Unknown delivery ID")
            if status == "sent":
                row = self.db.execute("SELECT onboarding FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
                if row["onboarding"]:
                    self.db.execute("INSERT OR REPLACE INTO meta VALUES ('initialized',?)", (utcnow().isoformat(),))

    def health(self, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('health',?)", (json.dumps(value, ensure_ascii=False),))

    def confirm_external(self, delivery_id, receipt):
        with self.db:
            row = self.db.execute("SELECT status,onboarding FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            previous = self.db.execute("SELECT provider_id FROM receipts WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None or row["status"] not in {"sending", "unknown", "sent"}:
                raise ValueError("Begin delivery before confirming a successful send")
            if row["status"] == "sent" and (not previous or previous[0] != receipt):
                raise ValueError("A different receipt is already recorded")
            self.db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?)", (delivery_id, receipt))
            self.db.execute("UPDATE deliveries SET status='sent',error='' WHERE id=?", (delivery_id,))
            if row["onboarding"]:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('initialized',?)", (utcnow().isoformat(),))

    def status(self):
        health = self.db.execute("SELECT value FROM meta WHERE key='health'").fetchone()
        return {"initialized": self.initialized,
                "deferred_reviews": self.db.execute("SELECT COUNT(*) FROM reviews WHERE status='deferred'").fetchone()[0],
                "review_decisions": self.db.execute("SELECT COUNT(*) FROM review_audit").fetchone()[0],
                "processed_versions": self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0],
                "deliveries": [{"id": d["id"], "status": d["status"], "message_id": d["payload"]["message_id"],
                                "attempts": d["attempts"], "error": d["error"],
                                "receipt_id": self.db.execute("SELECT provider_id FROM receipts WHERE delivery_id=?", (d["id"],)).fetchone()[0]
                                if self.db.execute("SELECT 1 FROM receipts WHERE delivery_id=?", (d["id"],)).fetchone() else None}
                               for d in self.deliveries()],
                "health": json.loads(health[0]) if health else None}
