import json
import smtplib
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from tibo_mailer.analysis import analyze, llm_event
from tibo_mailer.config import Config, address
from tibo_mailer.mail import DeliveryUnknown, build_mail, send_mail
from tibo_mailer.models import Event, FetchResult, Post
from tibo_mailer.service import check
from tibo_mailer.source import PROFILE, parse_posts
from tibo_mailer.state import State, process_lock
from tibo_mailer.timing import convert

NOW = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)


def post(text="Codex quotas will reset tomorrow before 10am PST.", identity="100", **kwargs):
    return Post(identity, text, NOW, **kwargs)


def article(identity="100", author="thsottiaux", text="Codex quotas reset tomorrow.", stamp=True, extra=""):
    time = '<time datetime="2026-10-23T06:00:00Z">date</time>' if stamp else "date"
    return f'<div data-href="/{author}/status/{identity}"><article><a href="/{author}">Author</a><a href="/{author}/status/{identity}">{time}</a><div data-testid="tweetText" dir="auto">{text}</div>{extra}</article></div>'


class SourceTests(unittest.TestCase):
    def test_verifies_author_and_timestamp(self):
        html = article() + article("101", "someone_else") + article("102", stamp=False)
        result = parse_posts(html, PROFILE)
        self.assertEqual([p.id for p in result], ["100"])
        self.assertEqual(result[0].created_at, NOW)

    def test_quoted_author_and_body_are_not_attributed_to_tibo(self):
        result = parse_posts(article(text="Interesting", extra=article("200", "someone_else", "Codex quotas have reset")), PROFILE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Interesting")

    def test_plain_reposts_are_ignored(self):
        html = article(extra='<div data-testid="socialContext">Tibo reposted</div>')
        self.assertEqual(parse_posts(html, PROFILE), [])

    def test_public_formatter_timestamp(self):
        html = '<article><a href="/thsottiaux">Tibo</a><a id="time-1" href="/thsottiaux/status/100">1h</a><script>run("time-1", {"timestamp":1792735200000})</script><div dir="auto">hello</div></article>'
        self.assertEqual(parse_posts(html, PROFILE)[0].created_at, NOW)

    def test_own_thread_reply_carries_context(self):
        html = article() + article("101", text="Landing tomorrow before 10am PST.")
        reply = next(p for p in parse_posts(html, PROFILE + "/status/100") if p.id == "101")
        self.assertEqual(reply.parent_id, "100")
        self.assertIn("Codex", reply.context_text)


class TimingTests(unittest.TestCase):
    def test_literal_pst_and_dst_alternative(self):
        timing = convert("tomorrow before 10am PST", NOW)
        self.assertIn("2026-10-24 02:00", timing.beijing)
        self.assertIn("2026-10-23 11:00", timing.pacific)
        self.assertIn("PDT", timing.pacific)
        self.assertIn("2026-10-24 01:00", timing.alternative)
        self.assertIn("按原文 PST", timing.note)
        self.assertTrue(timing.deadline)

    def test_relative_date_is_anchored_to_publication(self):
        dt = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)
        timing = convert("tomorrow before 10am PDT", dt)
        self.assertIn("2026-10-24 01:00", timing.beijing)

    def test_winter_pacific_time(self):
        timing = convert("2026-12-01 10am PT", NOW)
        self.assertIn("2026-12-02 02:00", timing.beijing)
        self.assertIn("PST", timing.pacific)
        self.assertEqual(timing.alternative, "")

    def test_dst_gap_and_fold_are_not_guessed(self):
        self.assertIsNone(convert("2026-03-08 2:30am PT", NOW).instant)
        self.assertIsNone(convert("2026-11-01 1:30am PT", NOW).instant)

    def test_missing_zone_or_date_is_not_guessed(self):
        self.assertIsNone(convert("tomorrow at 10am", NOW).instant)
        self.assertIsNone(convert("before 10am PST", NOW).instant)
        self.assertIsNone(convert("reset has completed", NOW).instant)

    def test_relative_hours_cross_day(self):
        timing = convert("in 2 hours", NOW)
        self.assertEqual(timing.instant, NOW + timedelta(hours=2))
        self.assertIn("2026-10-23 16:00", timing.beijing)


class AnalysisTests(unittest.TestCase):
    def test_main_and_own_reply_merge_to_one_event(self):
        parent = post("We shipped Codex improvements. Yes, that's a reset!")
        reply = post("Landing tomorrow before 10am PST.", "101", parent_id=parent.id, context_text=parent.text)
        events = analyze([parent, reply], Config())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].timing_post.id, "101")
        self.assertEqual(len(events[0].versions), 2)

    def test_credits_other_products_vague_and_speculation_rejected(self):
        for text in ["Codex reset soon", "API rate limits reset tomorrow", "Work quotas reset tomorrow",
                     "Codex billing credits reset tomorrow", "Maybe Codex quotas reset tomorrow",
                     "Codex quotas will not reset tomorrow", "If Codex quotas reset tomorrow, I will return"]:
            with self.subTest(text=text):
                self.assertEqual(analyze([post(text)], Config()), [])

    def test_explicit_completion_without_fake_reset_time(self):
        events = analyze([post("Codex quotas have been reset for everyone.")], Config())
        self.assertEqual(events[0].status, "completed")
        mail = build_mail(events, events[0].posts, NOW)
        self.assertIn("具体重置时刻未公布", mail["text"])
        self.assertIn("公告发布时间，不是重置时刻", mail["text"])

    def test_unknown_timezone_keeps_original_expression(self):
        p = post("Codex quotas will reset tomorrow at 10am.")
        mail = build_mail(analyze([p], Config()), [p], NOW)
        self.assertIn("具体重置时刻未公布", mail["text"])
        self.assertIn("tomorrow at 10am", mail["text"])

    def test_other_author_or_truncation_rejected(self):
        self.assertEqual(analyze([post(author="other")], Config()), [])
        self.assertEqual(analyze([post(truncated=True)], Config()), [])

    def test_negative_parent_does_not_turn_reply_into_an_alert(self):
        p = post("Tomorrow at 10am PST.", parent_id="99", context_text="Codex quotas will not reset tomorrow.")
        self.assertEqual(analyze([p], Config()), [])

    def test_newer_schedule_is_not_labeled_as_old_completion(self):
        parent = post("Codex quotas have been reset.")
        reply = replace(post("Landing tomorrow before 10am PST.", "101", parent_id=parent.id, context_text=parent.text),
                        created_at=NOW + timedelta(hours=1))
        event = analyze([parent, reply], Config())[0]
        self.assertEqual(event.status, "scheduled")
        self.assertEqual(event.timing_post.id, "101")

    def test_model_request_does_not_include_mail_credentials(self):
        config = Config(mode="llm", llm_model="configured-model", llm_api_key="model-secret",
                        mail_to="private-recipient@example.com", smtp_password="smtp-private-secret")
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"choices": [{"message": {"content": '{"qualifies": false}'}}]}).encode()
        with patch("tibo_mailer.analysis.urlopen", return_value=response) as request:
            self.assertIsNone(llm_event([post()], config))
        sent = request.call_args.args[0].data.decode()
        self.assertNotIn(config.mail_to, sent)
        self.assertNotIn(config.smtp_password, sent)

    def test_model_invented_quote_is_rejected(self):
        config = Config(mode="llm", llm_model="configured-model", llm_api_key="test-secret")
        content = {"qualifies": True, "status": "scheduled", "summary_zh": "重置预告", "timing_post_id": "100", "timing_quote": "in 999 hours"}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()
        with patch("tibo_mailer.analysis.urlopen", return_value=response):
            with self.assertRaises(ValueError):
                llm_event([post()], config)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), mail_to="reader@example.com", mail_from="sender@example.com",
                             smtp_host="smtp.example.com", smtp_username="sender@example.com", smtp_password="secret")
        self.posts = [post()]
        self.sent = []

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self):
        return FetchResult(self.posts, [], NOW)

    def send(self, payload, config):
        self.sent.append(payload)

    def test_first_summary_once_and_edited_post_reconsidered(self):
        check(self.config, fetcher=self.fetch, sender=self.send)
        check(self.config, fetcher=self.fetch, sender=self.send)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("首次汇总", self.sent[0]["subject"])
        self.posts = [replace(self.posts[0], text="Codex quotas will reset in 2 hours.")]
        check(self.config, fetcher=self.fetch, sender=self.send)
        self.assertEqual(len(self.sent), 2)

    def test_dry_run_never_sends_or_marks_processed(self):
        result = check(self.config, dry_run=True, fetcher=self.fetch, sender=self.send)
        self.assertTrue(result["ok"])
        state = State(self.config.data_dir)
        self.assertFalse(state.initialized)
        self.assertFalse(state.seen(self.posts[0].version))
        state.close()
        self.assertEqual(self.sent, [])

    def test_fetch_failure_does_not_initialize_or_mark_seen(self):
        def fail():
            raise RuntimeError("source unavailable")
        with self.assertRaises(RuntimeError):
            check(self.config, fetcher=fail, sender=self.send)
        state = State(self.config.data_dir)
        self.assertFalse(state.initialized)
        self.assertFalse(state.seen(self.posts[0].version))
        self.assertFalse(state.status()["health"]["source_ok"])
        state.close()

    def test_known_delivery_failure_retries_before_new_posts(self):
        def fail(*_):
            raise ConnectionError("secret-from-provider")
        result = check(self.config, fetcher=self.fetch, sender=fail)
        self.assertFalse(result["ok"])
        state = State(self.config.data_dir)
        self.assertFalse(state.initialized)
        self.assertNotIn("secret-from-provider", json.dumps(state.status()))
        state.close()
        check(self.config, fetcher=self.fetch, sender=self.send)
        self.assertEqual(len(self.sent), 1)

    def test_unknown_delivery_never_auto_retries(self):
        calls = []
        def ambiguous(*_):
            calls.append(1)
            raise DeliveryUnknown("timeout after DATA")
        check(self.config, fetcher=self.fetch, sender=ambiguous)
        check(self.config, fetcher=self.fetch, sender=ambiguous)
        self.assertEqual(len(calls), 1)
        state = State(self.config.data_dir)
        self.assertEqual(state.deliveries()[0]["status"], "unknown")
        state.update("initial-summary", "sent")
        self.assertTrue(state.initialized)
        state.close()
        check(self.config, fetcher=self.fetch, sender=self.send)
        self.assertEqual(self.sent, [])

    def test_crash_during_send_becomes_unknown(self):
        state = State(self.config.data_dir)
        payload = {"message_id": "<test@example.com>"}
        state.reserve("crash", payload, [])
        state.update("crash", "sending")
        state.close()
        state = State(self.config.data_dir)
        self.assertEqual(state.deliveries()[0]["status"], "unknown")
        state.close()

    def test_process_overlap_rejected(self):
        with process_lock(self.config.data_dir):
            with self.assertRaises(RuntimeError):
                with process_lock(self.config.data_dir):
                    pass


class MailTests(unittest.TestCase):
    def test_html_is_escaped_and_deadline_is_preserved(self):
        p = post("Codex reset tomorrow before 10am PST. <script>bad()</script>")
        mail = build_mail(analyze([p], Config()), [p], NOW)
        self.assertNotIn("<script>", mail["html"])
        self.assertIn("02:00前", mail["subject"])
        self.assertIn("此解释未经确认", mail["text"])

    def test_smtp_acceptance_survives_quit_failure(self):
        smtp = MagicMock()
        smtp.send_message.return_value = {}
        smtp.quit.side_effect = OSError("connection closed")
        payload = build_mail([], [], NOW)
        payload.update({"to": "reader@example.com", "from": "sender@example.com"})
        config = Config(mail_to=payload["to"], mail_from=payload["from"], smtp_host="smtp.example.com", smtp_username="sender", smtp_password="secret")
        with patch("tibo_mailer.mail.smtplib.SMTP_SSL", return_value=smtp):
            send_mail(payload, config)
        smtp.send_message.assert_called_once()

    def test_ambiguous_smtp_timeout(self):
        smtp = MagicMock()
        smtp.send_message.side_effect = TimeoutError()
        payload = build_mail([], [], NOW)
        payload.update({"to": "reader@example.com", "from": "sender@example.com"})
        config = Config(mail_to=payload["to"], mail_from=payload["from"], smtp_host="smtp.example.com", smtp_username="sender", smtp_password="secret")
        with patch("tibo_mailer.mail.smtplib.SMTP_SSL", return_value=smtp):
            with self.assertRaises(DeliveryUnknown):
                send_mail(payload, config)

    def test_header_injection_rejected(self):
        with self.assertRaises(ValueError):
            address("reader@example.com\r\nBcc: other@example.com")


if __name__ == "__main__":
    unittest.main()
