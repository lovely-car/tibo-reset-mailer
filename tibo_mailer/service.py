from __future__ import annotations

from .analysis import analyze
from .config import Config
from .mail import DeliveryUnknown, build_mail, send_mail
from .models import utcnow
from .source import fetch_posts
from .state import State, process_lock
from . import review as review_store


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


def check(config: Config, dry_run=False, fetcher=None, sender=send_mail):
    with process_lock(config.data_dir):
        state = State(config.data_dir)
        try:
            if not dry_run:
                config.validate_mail()
                dispatch(state, config, sender)
                unresolved = state.deliveries({"pending", "unknown"})
                if unresolved:
                    return {"ok": False, "reason": "Previous delivery unresolved", "delivery_ids": [d["id"] for d in unresolved]}
            result = fetcher() if fetcher else fetch_posts(retry_urls=review_store.retry_urls(state))
            if not result.posts and result.warnings:
                raise RuntimeError("No complete evidence is available; retry later")
            selected = result.posts[:20] if state.initialized else result.posts
            if dry_run:
                # A preview cannot mutate semantic decisions or the evidence archive.
                new = [p for p in selected if not state.seen(p.version)]
                evidence = selected
            else:
                new, evidence, reasons, fingerprints = review_store.collect(state, result)
            events = analyze(evidence, config)
            from .analysis import group_key, inventory
            by_id = inventory(evidence)
            active_roots = {group_key(p, by_id) for p in new}
            active_events = [e for e in events if group_key(e.timing_post, by_id) in active_roots]
            matched = {v for e in active_events for v in e.versions}
            events = [e for e in active_events if not state.event_claimed(e)]
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
                processed = [p.version for p in new if p.version in matched or not review_store.suspect(p)]
                if state.reserve(delivery_id, payload, processed, onboarding=True, event_keys=[e.key for e in events]):
                    reserved.append(delivery_id)
            else:
                for event in events:
                    payload = build_mail([event], event.posts, result.checked_at)
                    payload.update({"to": config.mail_to, "from": config.mail_from})
                    if state.reserve(event.key, payload, event.versions, event_keys=[event.key]):
                        reserved.append(event.key)
            # Dismiss only clearly non-candidates. Uncertain resets remain queued
            # even when an older client accidentally set their seen flag.
            for post in new:
                fingerprint = fingerprints[post.version]
                if post.version in matched or not review_store.suspect(post):
                    review_store.decision(state, post, fingerprint, "processed", "Validated event or unrelated post")
                    state.mark_seen([post.version])
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
