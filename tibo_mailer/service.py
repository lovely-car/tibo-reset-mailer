from __future__ import annotations

from .analysis import analyze
from .config import Config
from .mail import DeliveryUnknown, build_mail, send_mail
from .models import utcnow
from .source import fetch_posts
from .state import State, process_lock


def dispatch(state: State, config: Config, sender=send_mail, only_ids=None):
    """Retry known failures only. Unknown outcomes require a human to reconcile."""
    outcomes = []
    for record in state.deliveries({"pending"}):
        if only_ids is not None and record["id"] not in only_ids:
            continue
        state.update(record["id"], "sending")
        try:
            sender(record["payload"], config)
        except DeliveryUnknown:
            state.update(record["id"], "unknown", "SMTP outcome uncertain; verify sent mail before resolving")
        except Exception as exc:
            # Never persist a provider's raw exception, which could echo secrets.
            state.update(record["id"], "pending", type(exc).__name__)
        else:
            state.update(record["id"], "sent")
        outcomes.append(record["id"])
    return outcomes


def check(config: Config, dry_run=False, fetcher=fetch_posts, sender=send_mail):
    with process_lock(config.data_dir):
        state = State(config.data_dir)
        try:
            if not dry_run:
                config.validate_mail()
                dispatch(state, config, sender)
                unresolved = state.deliveries({"pending", "unknown"})
                if any(d["onboarding"] for d in unresolved):
                    return {"ok": False, "reason": "First summary delivery unresolved", "delivery_ids": [d["id"] for d in unresolved]}
            result = fetcher()
            selected = result.posts[:20] if state.initialized else result.posts
            new = [p for p in selected if not state.seen(p.version)]
            # Include already-seen parent/thread evidence when evaluating a new reply.
            roots = {p.root_id for p in new}
            evidence = [p for p in selected if p.root_id in roots]
            events = analyze(evidence, config)
            health = {"checked_at": result.checked_at.isoformat(), "source_ok": True,
                      "new_versions": len(new), "events": len(events), "warnings": result.warnings,
                      "seven_day_history_complete": False}
            state.health(health)
            if dry_run:
                return {"ok": True, "dry_run": True, **health,
                        "previews": [build_mail([e], e.posts, now=result.checked_at) for e in events]}
            onboarding = not state.initialized
            reserved = []
            if onboarding:
                payload = build_mail(events, selected, result.checked_at, onboarding=True)
                delivery_id = "initial-summary"
                payload.update({"to": config.mail_to, "from": config.mail_from})
                if state.reserve(delivery_id, payload, [p.version for p in selected], onboarding=True):
                    reserved.append(delivery_id)
            else:
                for event in events:
                    payload = build_mail([event], event.posts, result.checked_at)
                    payload.update({"to": config.mail_to, "from": config.mail_from})
                    if state.reserve(event.key, payload, event.versions):
                        reserved.append(event.key)
            state.mark_seen([p.version for p in new])
            dispatch(state, config, sender, only_ids=reserved)
            status = state.status()
            unresolved = [d for d in status["deliveries"] if d["status"] in {"pending", "unknown"}]
            return {"ok": not unresolved, **health, "initialized": state.initialized,
                    "needs_attention": unresolved}
        except Exception as exc:
            state.health({"checked_at": utcnow().isoformat(), "source_ok": False, "error_type": type(exc).__name__})
            raise
        finally:
            state.close()
