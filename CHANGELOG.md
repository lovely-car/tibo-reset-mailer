# Changelog

## 0.2.0-beta.1

- Copyable Codex deployment prompt, AGENTS.md routing and a complete personal-monitor setup guide.
- Agent commands expose verified evidence to Codex and format/reserve mail for connected-mail tools, without SMTP or model API configuration.
- Explicit delivery begin/confirm steps and durable provider email IDs; unknown outcomes require reconciliation before retry.
- Separate private agent data, recipient isolation, fresh-scan validation and no-new-post completion.
- The existing standalone SMTP route remains available. Host mail/scheduler availability is checked during setup; local operation still requires an online machine and running desktop host.

## 0.1.0-beta.1

- Standalone Python CLI and Docker Compose deployment, hourly by default.
- Direct official X public-page retrieval with author, timestamp and quote isolation.
- Conservative default rules and optional OpenAI-compatible model analysis.
- Chinese email with Beijing and Los Angeles times, deadlines and timezone ambiguity notes.
- One-time partial-history summary, post-version deduplication and durable retry queue.
- Manual reconciliation for ambiguous SMTP outcomes; no blind resend.
- Offline email preview, configuration wizard, health/status commands and cross-platform tests.
