"""Evidence and durable delivery hand-off for a Codex + connected-mail workflow.

This module never sends email, calls a model, or creates a scheduler by itself.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .analysis import COMPLETE, candidate, codex_context, group_key, inventory, series_anchors
from .config import address
from .mail import build_mail
from .models import Event, Post, utcnow
from .source import fetch_posts
from .state import State, process_lock
from .timing import TIME_SIGNAL
from . import review as review_store


def write_private(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    if os.name != "nt":
        path.chmod(0o600)


def post_from_dict(value):
    return Post.from_dict(value)


def init(directory: Path, recipient: str):
    recipient = address(recipient)
    with process_lock(directory):
        path = directory / "agent-config.json"
        if not path.exists() and any((directory / name).exists() for name in ("state.sqlite3", "state.json")):
            raise ValueError("Existing state belongs to another monitor; use a separate data directory")
        if path.exists() and json.loads(path.read_text(encoding="utf-8"))["recipient"] != recipient:
            raise ValueError("This directory belongs to another recipient; use a separate private data directory")
        write_private(path, {"recipient": recipient, "interval_seconds": 3600})
    return {"configured": True, "data_dir": str(directory.resolve()), "sends_email": False}


def scan(directory: Path, fetcher=None):
    with process_lock(directory):
        state = State(directory)
        try:
            unresolved = state.deliveries({"pending", "unknown"})
            if unresolved:
                return {"ok": True, "action": "resume_delivery", "deliveries": unresolved}
            try:
                result = fetcher() if fetcher else fetch_posts(retry_urls=review_store.retry_urls(state))
            except Exception as exc:
                state.health({"checked_at": utcnow().isoformat(), "source_ok": False, "error_type": type(exc).__name__})
                raise
            if not result.posts and result.warnings:
                state.health({"checked_at": result.checked_at.isoformat(), "source_ok": False,
                              "warnings": result.warnings, "error_type": "IncompleteEvidence"})
                raise RuntimeError("No complete evidence is available; retry later")
            fresh, evidence, reasons, fingerprints = review_store.collect(state, result)
            batch = {"scan_id": uuid.uuid4().hex, "checked_at": result.checked_at.isoformat(),
                     "initialized": state.initialized, "seven_day_history_complete": False,
                     "warnings": result.warnings, "new_versions": [p.version for p in fresh],
                     "posts": [p.public_dict() for p in evidence], "review_reasons": reasons,
                     "review_fingerprints": fingerprints}
            write_private(directory / "agent-scan.json", batch)
            state.health({"checked_at": result.checked_at.isoformat(), "source_ok": True,
                          "new_versions": len(fresh), "warnings": result.warnings})
            return {"ok": True, "action": "review" if fresh or not state.initialized else "quiet", **batch}
        finally:
            state.close()


def read_batch(directory, scan_id, state):
    batch = json.loads((directory / "agent-scan.json").read_text(encoding="utf-8"))
    if batch["scan_id"] != scan_id:
        raise ValueError("Scan ID changed; read the current scan before preparing mail")
    age = utcnow() - datetime.fromisoformat(batch["checked_at"])
    if age > timedelta(hours=1) or age < -timedelta(minutes=5):
        raise ValueError("Evidence expired; perform a fresh scan")
    if state.deliveries({"pending", "unknown"}):
        raise ValueError("Resolve the existing delivery before processing another batch")
    if state.initialized != batch["initialized"]:
        raise ValueError("Initialization changed; perform a fresh scan")
    if state.db.execute("SELECT 1 FROM processed_scans WHERE id=?", (scan_id,)).fetchone():
        raise ValueError("This scan has already been processed")
    return batch


def prepare(directory: Path, scan_id: str, review: dict):
    """Validate Codex's evidence, format times, and reserve one email digest."""
    with process_lock(directory):
        state = State(directory)
        try:
            batch = read_batch(directory, scan_id, state)
            posts = [post_from_dict(p) for p in batch["posts"]]
            by_id = {p.id: p for p in posts}
            if not isinstance(review, dict) or not isinstance(review.get("events"), list):
                raise ValueError("Review must contain an events array")
            events, used_roots = [], set()
            for item in review["events"]:
                if not isinstance(item, dict):
                    raise ValueError("Each event must be an object")
                ids = item.get("post_ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i in by_id for i in ids):
                    raise ValueError("Event references posts outside the verified scan")
                chosen = [by_id[i] for i in dict.fromkeys(ids)]
                roots = {group_key(p, inventory(posts)) for p in chosen}
                if len(roots) != 1 or roots & used_roots:
                    raise ValueError("Merge the same thread; keep unrelated threads separate")
                used_roots |= roots
                timing_post = by_id.get(item.get("timing_post_id"))
                quote, status, summary = item.get("timing_quote"), item.get("status"), item.get("summary_zh")
                if (timing_post not in chosen or not candidate(timing_post, chosen, posts) or not isinstance(quote, str)
                        or not quote or quote not in timing_post.text or status not in {"scheduled", "completed"}
                        or not isinstance(summary, str) or not summary or len(summary) > 1000):
                    raise ValueError("Event failed author, Codex context, source quote or summary validation")
                if not (TIME_SIGNAL.search(quote) if status == "scheduled" else COMPLETE.search(quote)):
                    raise ValueError("The source quote does not substantiate the claimed timing or completion")
                if not any(p.version in batch["new_versions"] for p in chosen):
                    raise ValueError("An alert must include an unprocessed post version")
                anchors = series_anchors(chosen, posts)
                if anchors and not codex_context("\n".join(p.text + "\n" + p.context_text for p in chosen)):
                    summary += " 结合已核验的此前 Codex 重置系列判断；本次短帖未重复写出 Codex。"
                events.append(Event(list({p.id: p for p in chosen + anchors}.values()), status, summary, quote, timing_post))
            if not events and state.initialized:
                raise ValueError("No matching events: use agent complete instead")
            matched = {v for event in events for v in event.versions}
            results = review_store.outcomes(batch, matched, review)
            events = [event for event in events if not state.event_claimed(event)]
            if not events and state.initialized:
                deferred = review_store.finish(state, batch, results)
                return {"action": "quiet", "already_notified": True, "deferred": deferred}
            config = json.loads((directory / "agent-config.json").read_text(encoding="utf-8"))
            payload = build_mail(events, posts, now=utcnow(), onboarding=not state.initialized)
            if len(events) > 1:
                payload["subject"] = ("首次汇总｜" if not state.initialized else "") + "Codex 额度重置提醒｜Tibo 更新（多组消息）"
            payload["to"] = address(config["recipient"])
            deferred = sum(status == "deferred" for _, status, _ in results)
            if deferred:
                note = f"另有 {deferred} 条疑似消息保留待复查，不计作已确认重置。"
                payload["text"] += "\n" + note
                payload["html"] = payload["html"].replace("</body>", "<p>" + note + "</p></body>")
            identity = "agent-" + scan_id
            resolved = [p.version for p, status, _ in results if status != "deferred"]
            if not state.reserve(identity, payload, resolved, onboarding=not state.initialized, event_keys=[event.key for event in events]):
                raise ValueError("Event already reserved; reconcile the existing delivery")
            review_store.finish(state, batch, results)
            return {"delivery_id": identity, "status": "pending", "payload": payload, "deferred": deferred}
        finally:
            state.close()


def complete(directory: Path, scan_id: str, review=None):
    with process_lock(directory):
        state = State(directory)
        try:
            batch = read_batch(directory, scan_id, state)
            if not state.initialized:
                raise ValueError("First scan requires a delivered summary, including a zero-match summary")
            results = review_store.outcomes(batch, review=review)
            deferred = review_store.finish(state, batch, results)
            return {"processed": len(results) - deferred, "deferred": deferred, "action": "quiet"}
        finally:
            state.close()


def delivery_action(directory, identity, action, receipt=None):
    with process_lock(directory):
        state = State(directory)
        try:
            row = next((d for d in state.deliveries() if d["id"] == identity), None)
            if row is None or not identity.startswith("agent-"):
                raise ValueError("Unknown agent delivery")
            if action == "begin":
                if row["status"] != "pending":
                    raise ValueError("Only pending mail can begin; reconcile unknown outcomes in sent mail first")
                state.update(identity, "sending")
                return {"delivery_id": identity, "action": "send_once", "payload": row["payload"]}
            if action == "confirm":
                if not receipt or not receipt.strip() or len(receipt) > 512:
                    raise ValueError("A provider's successful email ID is required")
                state.confirm_external(identity, receipt)
                return {"delivery_id": identity, "status": "sent", "receipt_id": receipt}
            if action == "failed":
                if row["status"] not in {"unknown", "pending"}:
                    raise ValueError("Cannot mark a sent email as failed")
                state.update(identity, "pending", "Provider explicitly reported no send; retry permitted")
                return {"delivery_id": identity, "status": "pending"}
            raise ValueError("Unknown delivery action")
        finally:
            state.close()


def add_arguments(sub):
    parser = sub.add_parser("agent", help="供 Codex 判断与连接邮箱投递；不需要 SMTP 或模型 API 配置")
    parser.add_argument("--data-dir", type=Path, default=Path(".tibo-mailer-agent"))
    commands = parser.add_subparsers(dest="agent_command", required=True)
    configure = commands.add_parser("init")
    configure.add_argument("--recipient", required=True)
    commands.add_parser("scan")
    commands.add_parser("status")
    review = commands.add_parser("prepare")
    review.add_argument("--scan-id", required=True)
    review.add_argument("--input", type=Path, required=True)
    done = commands.add_parser("complete")
    done.add_argument("--scan-id", required=True)
    done.add_argument("--input", type=Path, help="Optional deferred/excluded decisions with reasons")
    for name in ("begin", "confirm", "failed"):
        action = commands.add_parser(name)
        action.add_argument("delivery_id")
        if name == "confirm":
            action.add_argument("--receipt-id", required=True)
        if name == "failed":
            action.add_argument("--known-not-sent", action="store_true", required=True)


def run(args):
    directory, command = args.data_dir, args.agent_command
    if command == "init":
        return init(directory, args.recipient)
    if not (directory / "agent-config.json").exists():
        raise ValueError("Run agent init --recipient EMAIL first")
    if command == "scan":
        return scan(directory)
    if command == "prepare":
        return prepare(directory, args.scan_id, json.loads(args.input.read_text(encoding="utf-8-sig")))
    if command == "complete":
        return complete(directory, args.scan_id, json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else None)
    if command == "status":
        with process_lock(directory):
            state = State(directory)
            try:
                return state.status()
            finally:
                state.close()
    return delivery_action(directory, args.delivery_id, command, getattr(args, "receipt_id", None))
