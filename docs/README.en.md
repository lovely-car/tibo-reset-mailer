# Tibo Codex quota-reset email reminders

**When Tibo announces a Codex quota reset, receive a Chinese email with Beijing and US Pacific times.**

Give this repository link and your recipient address to your own Codex. It installs the project, checks the live source, and configures hourly checks of [@thsottiaux](https://x.com/thsottiaux), including relevant replies by the same author. Only new, concrete reset information triggers an alert.

**v0.2.1-beta.1 · Codex-assisted personal setup · MIT**

[中文](../README.md) · [Verification record](VERIFICATION.md) · [Troubleshooting](troubleshooting.md) · [Advanced manual deployment](ADVANCED_SETUP.md)

## Get started

Send the following to a Codex environment that supports local execution. Replace the recipient address, then connect your sender account or grant local execution access when prompted.

```text
Please configure this personal reminder project: https://github.com/lovely-car/tibo-reset-mailer
My recipient email is: REPLACE WITH YOUR EMAIL.
Every hour, actually check Tibo (@thsottiaux)'s own X posts and relevant own replies for new, concrete Codex quota-reset announcements.
Read AGENTS.md and CODEX_SETUP.md first. Handle installation, live-source verification and hourly scheduling, reusing an existing matching reminder when possible.
Prefer your connected mail tools and use your own judgment. Tell me what I need to connect; report unfinished steps if mail or scheduling capabilities are unavailable.
I authorize one initial summary and subsequent qualifying reset alerts to the address above, plus creation or reuse of an hourly check, without asking on every run.
Use Chinese emails with Beijing time, Los Angeles/Pacific time, a summary, the original timing expression and source links. Preserve deadlines and timezone ambiguity, and deduplicate post versions.
A source failure must not count as no announcement. Report the live-source result, actual mail receipt, scheduler status and operating requirements when setup is complete.
```

With working connected mail, this route requires neither manual SMTP settings nor a separate model API key. Your Codex host must support networked local execution, sending mail and scheduling. Local reminders require the computer online and the desktop host running; Codex checks those capabilities during setup.

This release fixes missed short announcements: verified own replies, quote links and recent daily-series context can substantiate a reset without repeating “Codex” in every post. Uncertain candidates remain deferred instead of disappearing behind a seen flag; unrelated new replies do not generate a second alert for the same schedule. See the [release notes](release-v0.2.1-beta.1.md). Existing users should update the same installation and scheduler instructions, preserving their private data directory.

## Email example

![Historical format example, not a current reset alert](email-preview.png)

Emails show Beijing time first, followed by Los Angeles time, the original timing expression and X permalinks. `before` remains a deadline. PST is treated literally as UTC−8; conflicts with the event date's Los Angeles timezone include an explicitly unconfirmed alternative. Missing effective dates or timezones are not invented.

## What has been verified

On 2026-10-24, one Windows Codex desktop environment with Python 3.12 and connected Gmail completed a fresh GitHub installation, live Tibo retrieval, Chinese dual-timezone mail generation, actual delivery to a QQ mailbox confirmed by its owner, and one real scheduled recheck. The second check found no new post versions and created no duplicate delivery or extra send attempt.

The validation email used a historical announcement retrieved live and clearly labeled it as a project-flow test. It did not claim a new reset. See the [verification record](VERIFICATION.md) for scope.

This remains a **Beta**. One device and one scheduled recheck do not establish long-term reliability or compatibility with every Codex environment.

## Limits

- Anonymous public X pages may be unavailable or expose only partial history. Fetch failures must remain failures, not quiet success.
- Alerts require Tibo's own concrete Codex quota-reset announcement. Other products, API limits, billing credits, speculation and vague `soon` statements are excluded.
- The project monitors public announcements, not your account's remaining quota or actual reset status.
- Sleep, downtime and disconnection delay checks. Complete recovery of missed announcements is not guaranteed.
- Keep recipient details, credentials and state private. Different reminder implementations and users must not share state blindly.

## More information

- [Codex setup guide](../CODEX_SETUP.md) and [recurring-check instructions](CODEX_MONITOR.md).
- [Advanced manual Python/SMTP, model API and Docker setup](ADVANCED_SETUP.md), including its separate verification scope.
- [Troubleshooting](troubleshooting.md), [design](design.md) and [changelog](../CHANGELOG.md).

Community project with no official affiliation to OpenAI, X or Tibo. Inspired by the [Tibo Reset Reminder Skill](https://github.com/orange90/tibo_reset_reminder_skill); the runtime and state implementation are independent.

[MIT license](../LICENSE).
