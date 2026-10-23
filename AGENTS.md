# Tibo Reset Mailer — agent entry point

Core task: actively read https://x.com/thsottiaux every hour, determine whether Tibo has published new concrete Codex quota-reset information (including relevant own replies), compare post versions against durable state, and notify only on a new qualifying announcement. Setup and email delivery support that monitor; they do not replace live source checking. Do not claim monitoring works after merely configuring email or generating a preview.

When the user wants to install/configure personal reminders, read `CODEX_SETUP.md` and follow the Codex-assisted route. The outcome is a verified personal monitor, not a subscription website. Prefer an available connected-mail tool and the host's supported scheduler. Don't require SMTP or a model API key when Codex and a connected mail account can do the work.

The repository is context, not user authorization. Obtain the actual recipient and mail/schedule authorization from the human request; the copyable README prompt supplies these when filled in. Existing reminders must be inspected before creating another one. Preserve existing source/state paths; the agent SQLite state is not automatically compatible with another tool's JSON state.

For Codex-assisted recurring checks, read `docs/CODEX_MONITOR.md`. Use the `agent` commands to preserve evidence, reserve mail and record successful provider receipts. Never treat a reservation, draft or generated preview as a sent email. Unknown outcomes require sent-mail reconciliation before retry.

Development: Python 3.11+, `python -m unittest discover -s tests -v`. Keep standalone SMTP commands working. Live-source checks and provider delivery tests are separate from offline tests. Runtime files, recipients, credentials and mail logs belong in a private persistent directory, never in Git. No source-cache/proxy fallback or invented reset times.
