"""Regression cases from public short reset announcements; no private runtime data."""
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tibo_mailer import agent, review
from tibo_mailer.analysis import analyze, candidate
from tibo_mailer.config import Config
from tibo_mailer.models import FetchResult, Post
from tibo_mailer.service import check
from tibo_mailer.source import PROFILE, fetch_posts, parse_posts
from tibo_mailer.state import State
from tibo_mailer.timing import convert

NOW = datetime(2026, 10, 25, 6, 30, tzinfo=timezone.utc)
ROOT = Post("200", "Day 20/ Rest and reset", NOW - timedelta(hours=3))
REPLY = Post("201", "By midnight PST", NOW - timedelta(hours=3) + timedelta(seconds=30),
             parent_id=ROOT.id, context_text=ROOT.text)
ANCHOR = Post("180", "Day 18/ Codex improvements. That's a reset!", NOW - timedelta(days=2))
COMPLETE = Post("202", "It is all propagated. Enjoy some mad codexing tomorrow.", NOW,
                quoted_post=replace(REPLY, parent_id=None, context_text=""))


def html_post(pid, text, extra="", author="thsottiaux"):
    return f'<div data-href="/{author}/status/{pid}"><article><a href="/{author}">author</a>' + \
        f'<a href="/{author}/status/{pid}"><time datetime="2026-10-25T03:30:00Z">date</time></a>' + \
        f'<div data-testid="tweetText">{text}</div>{extra}</article></div>'


class QuoteAndTimingTests(unittest.TestCase):
    def test_quote_page_binds_own_body_to_own_timestamp_permalink(self):
        quote = html_post("201", "By midnight PST")
        page = '<article><a href="/thsottiaux">Tibo</a><div data-testid="tweetText">It is all propagated. Enjoy codexing.</div>' + \
            '<a href="/thsottiaux/status/201"></a>' + quote + \
            '<a href="/thsottiaux/status/202"><time datetime="2026-10-25T06:30:00Z">date</time></a></article>'
        posts = parse_posts(page, PROFILE + "/status/202")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].id, "202")
        self.assertEqual(posts[0].created_at, NOW)
        self.assertEqual(posts[0].quoted_post.id, "201")
        self.assertEqual(posts[0].quoted_post.text, "By midnight PST")

    def test_foreign_quote_cannot_become_authoritative_context(self):
        posts = parse_posts(html_post("202", "Interesting", html_post("999", "Codex reset now", author="other")), PROFILE)
        self.assertEqual(len(posts), 1)
        self.assertIsNone(posts[0].quoted_post)
        self.assertEqual(analyze(posts, Config()), [])

    def test_pending_thread_is_fetched_after_leaving_profile(self):
        def fetch(url):
            if url == PROFILE:
                return html_post("300", "Coffee in SF")
            self.assertEqual(url, ROOT.url)
            return html_post("200", ROOT.text) + html_post("201", REPLY.text)
        with patch("tibo_mailer.source.fetch_html", side_effect=fetch):
            result = fetch_posts(NOW, retry_urls=[ROOT.url])
        self.assertEqual(result.warnings, [])
        self.assertEqual({p.id for p in result.posts}, {"200", "201", "300"})

    def test_midnight_literal_pst_and_actual_local_alternative(self):
        timing = convert("By midnight PST", REPLY.created_at)
        self.assertEqual(timing.instant, datetime(2026, 10, 25, 8, tzinfo=timezone.utc))
        self.assertIn("2026-10-25 16:00", timing.beijing)
        self.assertIn("2026-10-25 01:00", timing.pacific)
        self.assertIn("2026-10-25 15:00", timing.alternative)
        self.assertIn("未经确认", timing.alternative)
        self.assertTrue(timing.deadline)

    def test_midnight_without_zone_is_not_invented(self):
        self.assertIsNone(convert("By midnight", NOW).instant)


class ContextTests(unittest.TestCase):
    def test_daily_short_title_requires_verified_codex_anchor(self):
        self.assertEqual(analyze([ROOT, REPLY], Config()), [])
        events = analyze([ANCHOR, ROOT, REPLY], Config())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "scheduled")
        self.assertIn("此前 Codex", events[0].summary)

    def test_old_or_other_authors_anchor_is_insufficient(self):
        for anchor in [replace(ANCHOR, author="other"), replace(ANCHOR, created_at=NOW - timedelta(days=20))]:
            self.assertEqual(analyze([anchor, ROOT, REPLY], Config()), [])

    def test_daily_other_product_is_not_reclassified_as_codex(self):
        root = replace(ROOT, text="Day 20/ Work quotas reset")
        reply = replace(REPLY, context_text=root.text)
        self.assertEqual(analyze([ANCHOR, root, reply], Config()), [])

    def test_own_quote_connects_codexing_completion_to_reset(self):
        events = analyze([ROOT, REPLY, COMPLETE], Config())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "completed")
        self.assertEqual(events[0].timing_post.id, COMPLETE.id)

    def test_propagation_alone_without_reset_context_does_not_match(self):
        self.assertEqual(analyze([replace(COMPLETE, quoted_post=None)], Config()), [])

    def test_unrelated_own_reply_does_not_change_event_identity(self):
        main = Post("400", "Codex quotas will reset tomorrow before 10am PST", NOW)
        thanks = Post("401", "Thanks!", NOW + timedelta(minutes=1), parent_id=main.id, context_text=main.text)
        self.assertEqual(analyze([main], Config())[0].key, analyze([main, thanks], Config())[0].key)


class DeferredAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        agent.init(self.directory, "reader@example.com")
        state = State(self.directory)
        with state.db:
            state.db.execute("INSERT INTO meta VALUES ('initialized','test')")
        state.close()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, posts):
        return agent.scan(self.directory, lambda: FetchResult(posts, [], NOW))

    def test_uncertain_candidate_survives_complete_and_old_mark_seen(self):
        batch = self.scan([ROOT, REPLY])
        result = agent.complete(self.directory, batch["scan_id"])
        self.assertEqual(result["deferred"], 2)
        state = State(self.directory)
        state.mark_seen([ROOT.version, REPLY.version])
        state.close()
        again = self.scan([ROOT, REPLY])
        self.assertEqual(again["action"], "review")
        self.assertEqual(set(again["new_versions"]), {ROOT.version, REPLY.version})

    def test_legacy_filtered_reset_is_reconsidered_once_on_upgrade(self):
        state = State(self.directory)
        state.mark_seen([ROOT.version])
        state.close()
        batch = self.scan([ROOT])
        self.assertEqual(batch["review_reasons"][ROOT.version], "legacy-recheck")

    def test_context_change_reconsiders_an_unchanged_main_post(self):
        batch = self.scan([ROOT])
        agent.complete(self.directory, batch["scan_id"], {"excluded": [{"post_id": ROOT.id, "reason": "Initial evidence review", "category": "not-a-reset"}]})
        batch = self.scan([ROOT, REPLY])
        self.assertEqual(batch["review_reasons"][ROOT.version], "context-changed")

    def test_source_failure_keeps_deferred_evidence(self):
        self.scan([ROOT, REPLY])
        def fail():
            raise ConnectionError("offline")
        with self.assertRaises(ConnectionError):
            agent.scan(self.directory, fail)
        state = State(self.directory)
        self.assertEqual(state.status()["deferred_reviews"], 2)
        self.assertFalse(state.seen(ROOT.version))
        state.close()

    def test_quote_does_not_erase_archived_thread_context(self):
        batch = self.scan([ROOT, REPLY])
        agent.complete(self.directory, batch["scan_id"])
        # Only the completion quote is visible now; the old thread context was
        # verified previously and must survive its compact embedded reply card.
        batch = self.scan([COMPLETE])
        self.assertEqual(batch["new_versions"], [COMPLETE.version])
        result = agent.prepare(self.directory, batch["scan_id"], {"events": [{"post_ids": [ROOT.id, REPLY.id, COMPLETE.id],
            "timing_post_id": COMPLETE.id, "status": "completed", "summary_zh": "完成确认", "timing_quote": "It is all propagated."}]})
        self.assertEqual(result["status"], "pending")

    def test_review_audit_has_reasons_and_no_recipient(self):
        batch = self.scan([ROOT])
        agent.complete(self.directory, batch["scan_id"], {"deferred": [{"post_id": ROOT.id, "reason": "Need own timing or completion"}]})
        state = State(self.directory)
        rows = [dict(r) for r in state.db.execute("SELECT * FROM review_audit")]
        self.assertIn("Need own timing", json.dumps(rows))
        self.assertNotIn("reader@example.com", json.dumps(rows))
        state.close()

    def test_unknown_exclusion_category_rejected(self):
        batch = self.scan([ROOT])
        with self.assertRaises(ValueError):
            agent.complete(self.directory, batch["scan_id"], {"excluded": [{"post_id": ROOT.id, "reason": "No Codex keyword", "category": "uncertain"}]})

    def test_quote_completion_can_be_prepared_and_other_candidate_stays_pending(self):
        other = Post("600", "Codex reset soon", NOW)
        batch = self.scan([ROOT, REPLY, COMPLETE, other])
        result = agent.prepare(self.directory, batch["scan_id"], {"events": [{"post_ids": [ROOT.id, REPLY.id, COMPLETE.id],
            "timing_post_id": COMPLETE.id, "status": "completed", "summary_zh": "本人完成确认。", "timing_quote": "It is all propagated."}]})
        self.assertEqual(result["deferred"], 1)
        self.assertIn("具体重置时刻未公布", result["payload"]["text"])
        agent.delivery_action(self.directory, result["delivery_id"], "begin")
        agent.delivery_action(self.directory, result["delivery_id"], "confirm", "fixture-provider-receipt")
        state = State(self.directory)
        self.assertFalse(state.seen(other.version))
        self.assertEqual(state.status()["deferred_reviews"], 1)
        state.close()

    def test_new_greeting_does_not_prepare_a_second_delivery(self):
        main = Post("400", "Codex quotas reset tomorrow before 10am PST", NOW)
        batch = self.scan([main])
        data = {"events": [{"post_ids": [main.id], "timing_post_id": main.id, "status": "scheduled", "summary_zh": "预告", "timing_quote": "tomorrow before 10am PST"}]}
        delivery = agent.prepare(self.directory, batch["scan_id"], data)
        agent.delivery_action(self.directory, delivery["delivery_id"], "begin")
        agent.delivery_action(self.directory, delivery["delivery_id"], "confirm", "fixture-provider-receipt")
        thanks = Post("401", "Thanks!", NOW, parent_id=main.id, context_text=main.text)
        batch = self.scan([main, thanks])
        data["events"][0]["post_ids"].append(thanks.id)
        self.assertEqual(agent.prepare(self.directory, batch["scan_id"], data)["action"], "quiet")
        self.assertEqual(self.scan([main, thanks])["action"], "quiet")
        state = State(self.directory)
        self.assertEqual(len(state.deliveries()), 1)
        state.close()


class StandaloneRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), mail_to="reader@example.com", mail_from="sender@example.com",
                             smtp_host="smtp.example.com", smtp_username="sender", smtp_password="fixture")

    def tearDown(self):
        self.temp.cleanup()

    def test_warning_only_first_fetch_does_not_initialize_or_send(self):
        sent = []
        with self.assertRaises(RuntimeError):
            check(self.config, fetcher=lambda: FetchResult([], ["Thread unavailable"], NOW), sender=lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        state = State(self.config.data_dir)
        self.assertFalse(state.initialized)
        self.assertEqual(state.deliveries(), [])
        state.close()

    def test_new_greeting_does_not_send_duplicate_smtp_mail(self):
        main = Post("400", "Codex quotas reset tomorrow before 10am PST", NOW)
        sent = []
        sender = lambda payload, _: sent.append(payload)
        check(self.config, fetcher=lambda: FetchResult([main], [], NOW), sender=sender)
        thanks = Post("401", "Thanks!", NOW, parent_id=main.id, context_text=main.text)
        check(self.config, fetcher=lambda: FetchResult([main, thanks], [], NOW), sender=sender)
        self.assertEqual(len(sent), 1)
        state = State(self.config.data_dir)
        self.assertTrue(state.seen(thanks.version))
        state.close()

    def test_existing_sqlite_initialization_seen_and_receipt_survive_upgrade(self):
        root = self.config.data_dir
        with sqlite3.connect(root / "state.sqlite3") as db:
            db.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE seen (version TEXT PRIMARY KEY, at TEXT NOT NULL);
                CREATE TABLE deliveries (id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
                  versions TEXT NOT NULL, onboarding INTEGER NOT NULL, at TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
                CREATE TABLE receipts (delivery_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL);
                INSERT INTO meta VALUES ('initialized','legacy-date');
                INSERT INTO seen VALUES ('legacy-version','legacy-date');
                INSERT INTO receipts VALUES ('legacy-delivery','fixture-provider-receipt');
            """)
        db.close()
        state = State(root)
        self.assertTrue(state.initialized)
        self.assertTrue(state.seen("legacy-version"))
        self.assertEqual(state.db.execute("SELECT provider_id FROM receipts").fetchone()[0], "fixture-provider-receipt")
        self.assertEqual(state.status()["deferred_reviews"], 0)
        state.close()


if __name__ == "__main__":
    unittest.main()
