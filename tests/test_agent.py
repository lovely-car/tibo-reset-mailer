import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from tibo_mailer import agent
from tibo_mailer.models import FetchResult, Post, utcnow
from tibo_mailer.state import State


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.now = utcnow()
        self.post = Post("100", "Codex quotas will reset tomorrow before 10am PST.", self.now)
        agent.init(self.directory, "reader@example.com")

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, posts=None):
        return agent.scan(self.directory, lambda: FetchResult([self.post] if posts is None else posts, [], self.now))

    def review(self):
        return {"events": [{"post_ids": ["100"], "timing_post_id": "100", "status": "scheduled",
                            "summary_zh": "Codex 重置预告。", "timing_quote": "tomorrow before 10am PST"}]}

    def reserve(self):
        batch = self.scan()
        return agent.prepare(self.directory, batch["scan_id"], self.review())["delivery_id"]

    def deliver(self, identity):
        agent.delivery_action(self.directory, identity, "begin")
        agent.delivery_action(self.directory, identity, "confirm", "provider-email-id")

    def test_full_flow_receipt_and_no_duplicate(self):
        identity = self.reserve()
        state = State(self.directory)
        self.assertFalse(state.initialized)
        state.close()
        self.deliver(identity)
        state = State(self.directory)
        self.assertTrue(state.initialized)
        self.assertEqual(state.status()["deliveries"][0]["receipt_id"], "provider-email-id")
        state.close()
        self.assertEqual(self.scan()["action"], "quiet")

    def test_pending_is_returned_before_fetching(self):
        identity = self.reserve()
        def forbidden_fetch():
            self.fail("Pending mail must be handled before another source fetch")
        result = agent.scan(self.directory, forbidden_fetch)
        self.assertEqual(result["action"], "resume_delivery")
        self.assertEqual(result["deliveries"][0]["id"], identity)

    def test_pending_is_not_success_and_confirm_requires_begin(self):
        identity = self.reserve()
        with self.assertRaises(ValueError):
            agent.delivery_action(self.directory, identity, "confirm", "not-yet-sent")
        state = State(self.directory)
        self.assertFalse(state.initialized)
        state.close()

    def test_interrupted_begin_is_unknown_and_not_restarted(self):
        identity = self.reserve()
        agent.delivery_action(self.directory, identity, "begin")
        result = self.scan()
        self.assertEqual(result["deliveries"][0]["status"], "unknown")
        with self.assertRaises(ValueError):
            agent.delivery_action(self.directory, identity, "begin")
        agent.delivery_action(self.directory, identity, "confirm", "found-in-sent")

    def test_definite_failure_can_be_retried(self):
        identity = self.reserve()
        agent.delivery_action(self.directory, identity, "begin")
        agent.delivery_action(self.directory, identity, "failed")
        self.deliver(identity)
        self.assertEqual(self.scan()["action"], "quiet")

    def test_receipt_confirmation_is_idempotent(self):
        identity = self.reserve()
        self.deliver(identity)
        agent.delivery_action(self.directory, identity, "confirm", "provider-email-id")
        with self.assertRaises(ValueError):
            agent.delivery_action(self.directory, identity, "confirm", "different-id")

    def test_initial_zero_summary_required(self):
        batch = self.scan([Post("200", "A normal product update", self.now)])
        with self.assertRaises(ValueError):
            agent.complete(self.directory, batch["scan_id"])
        record = agent.prepare(self.directory, batch["scan_id"], {"events": []})
        self.assertIn("部分历史", record["payload"]["text"])
        self.deliver(record["delivery_id"])

    def test_nonmatching_new_posts_are_marked_after_review(self):
        self.deliver(self.reserve())
        normal = Post("200", "A normal product update", self.now)
        batch = self.scan([normal])
        agent.complete(self.directory, batch["scan_id"])
        self.assertEqual(self.scan([normal])["action"], "quiet")

    def test_invented_time_is_rejected_without_marking_seen(self):
        batch = self.scan()
        review = self.review()
        review["events"][0]["timing_quote"] = "in 999 hours"
        with self.assertRaises(ValueError):
            agent.prepare(self.directory, batch["scan_id"], review)
        state = State(self.directory)
        self.assertFalse(state.seen(self.post.version))
        state.close()

    def test_expired_or_changed_scan_cannot_prepare(self):
        batch = self.scan()
        with patch("tibo_mailer.agent.utcnow", return_value=self.now + timedelta(hours=2)):
            with self.assertRaises(ValueError):
                agent.prepare(self.directory, batch["scan_id"], self.review())
        with self.assertRaises(ValueError):
            agent.prepare(self.directory, "wrong-scan", self.review())

    def test_recipient_isolation(self):
        with self.assertRaises(ValueError):
            agent.init(self.directory, "someone-else@example.com")

    def test_unrelated_existing_state_is_not_adopted(self):
        other = self.directory / "existing-monitor"
        other.mkdir()
        (other / "state.json").write_text('{}')
        with self.assertRaises(ValueError):
            agent.init(other, "reader@example.com")

    def test_fetch_failure_preserves_initialization_and_dedup(self):
        def fail():
            raise ConnectionError()
        with self.assertRaises(ConnectionError):
            agent.scan(self.directory, fail)
        state = State(self.directory)
        self.assertFalse(state.initialized)
        self.assertFalse(state.seen(self.post.version))
        state.close()


if __name__ == "__main__":
    unittest.main()
