# Tibo Reset Mailer

Self-hosted Chinese email alerts for concrete Codex quota-reset announcements by [@thsottiaux](https://x.com/thsottiaux). Beijing time comes first, followed by Los Angeles time, the original timing expression and X permalinks.

This is a community Beta, not an official OpenAI or X service. It monitors public announcements, not your account's remaining quota. Run it on an always-on computer or server; GitHub hosts the source, not a public subscription service.

## Quick start

Use Python 3.11+ in a virtual environment, then:

```bash
python -m pip install -e .
python -m tibo_mailer setup
python -m tibo_mailer doctor
python -m tibo_mailer check --dry-run
python -m tibo_mailer test-email
python -m tibo_mailer run
```

`setup` asks for your recipient, sender and SMTP app password. `.env.example` lists every option. Default mode `rules` needs no model API. Optional `llm` mode requires your own HTTPS OpenAI-compatible Chat Completions endpoint, model and API key. Only public post evidence is sent to the model.

The first successful run sends one partial-history summary. Later runs check hourly and deduplicate by post ID plus text hash. Public anonymous X pages do not provide complete seven-day coverage. Fetch failures never count as "no announcement".

PST is interpreted literally as UTC−8; PDT as UTC−7; PT follows the event date's IANA Los Angeles timezone. Conflicts are shown with an explicitly unconfirmed alternative. Missing dates or timezones are not invented. "Before" is a deadline, not an exact reset instant.

Persistent private state lives in `.tibo-mailer/state.sqlite3`. Keep it across restarts. Do not upload it, your `.env`, recipient details or credentials. Docker Compose uses a named volume:

```bash
docker compose up -d --build
docker compose exec mailer python -m tibo_mailer status
```

Known SMTP failures retry; ambiguous outcomes become `unknown` and do not auto-resend. Check provider/sent-mail records using the recorded Message-ID before `resolve DELIVERY_ID --sent` or `--retry`. `sent` means SMTP acceptance, not guaranteed inbox delivery.

CI tests Linux and Windows. The manual GitHub source-check workflow sends no mail. Hosted scheduled delivery is deliberately not configured without durable private state.

See the [Chinese guide](../README.md), [troubleshooting](troubleshooting.md), [design](design.md) and [MIT license](../LICENSE). Inspired by the Tibo Reset Reminder Skill; the runtime and state implementation are independent and do not import its code.
