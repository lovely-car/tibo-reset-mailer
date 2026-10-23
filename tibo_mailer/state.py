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

    def reserve(self, delivery_id, payload, versions, onboarding=False):
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO deliveries(id,status,payload,versions,onboarding,at) VALUES (?,'pending',?,?,?,?)",
                                     (delivery_id, json.dumps(payload, ensure_ascii=False), json.dumps(versions), int(onboarding), utcnow().isoformat()))
            self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(v, utcnow().isoformat()) for v in versions])
        return cursor.rowcount == 1

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

    def status(self):
        health = self.db.execute("SELECT value FROM meta WHERE key='health'").fetchone()
        return {"initialized": self.initialized,
                "processed_versions": self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0],
                "deliveries": [{"id": d["id"], "status": d["status"], "message_id": d["payload"]["message_id"],
                                "attempts": d["attempts"], "error": d["error"]} for d in self.deliveries()],
                "health": json.loads(health[0]) if health else None}
