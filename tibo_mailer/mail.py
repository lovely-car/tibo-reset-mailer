from __future__ import annotations

import hashlib
import html
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from .config import Config
from .models import Event, Post, utcnow
from .timing import BEIJING, PACIFIC, convert, format_time


class DeliveryUnknown(RuntimeError):
    pass


def build_mail(events: list[Event], posts: list[Post], now=None, onboarding=False, demo=False):
    now = now or utcnow()
    blocks, text = [], []
    first_time = None
    if demo:
        text.append("这是一封格式演示，不是新的重置公告。")
    if onboarding:
        count = sum(e.status == "completed" for e in events)
        earliest = min((p.created_at for p in posts), default=None)
        coverage = "当前公开页面只有部分历史，无法提供完整七天统计。"
        if earliest:
            coverage += " 本次最早可核验记录：" + format_time(earliest, BEIJING) + "。"
        text.extend([f"首次扫描：在可核验记录中识别出 {count} 组 Codex 重置已完成确认（同帖合并；不同帖是否指向同一事件未作推断）。", coverage])
        blocks.append("<p>" + html.escape(text[-2]) + "</p><p class='muted'>" + html.escape(coverage) + "</p>")
    for event in events:
        timing = convert(event.timing_quote, event.timing_post.created_at)
        first_time = first_time or timing
        state = "已完成确认" if event.status == "completed" else "重置预告"
        if event.status == "scheduled" and timing.instant and timing.instant < now:
            state = "原定预告时间已过，尚无本条对应的完成确认"
        prefix = "预计 " if event.status == "scheduled" and timing.instant else ""
        lines = ["重置北京时间：" + prefix + timing.beijing,
                 "重置美国时间：" + prefix + timing.pacific,
                 "原帖时间表述：" + event.timing_quote,
                 "状态：" + state, event.summary]
        if timing.note:
            lines.append(timing.note)
        if timing.alternative:
            lines.append(timing.alternative)
        if not timing.instant:
            lines.extend(["以下为公告发布时间，不是重置时刻：",
                          format_time(event.timing_post.created_at, BEIJING),
                          format_time(event.timing_post.created_at, PACIFIC)])
        links = list(dict.fromkeys([p.url for p in event.posts] + [
            f"https://x.com/thsottiaux/status/{p.parent_id}" for p in event.posts if p.parent_id]))
        lines.extend("来源：" + url for url in links)
        text.extend(lines + [""])
        esc = html.escape
        blocks.append(f"<section><span class='badge'>{esc(state)}</span>"
                      f"<h2>重置北京时间</h2><div class='clock'>{esc(prefix + timing.beijing)}</div>"
                      f"<p><strong>重置美国时间：</strong>{esc(prefix + timing.pacific)}</p>"
                      f"<p>{esc(event.summary)}</p><blockquote><strong>原帖时间表述</strong><br>{esc(event.timing_quote)}</blockquote>"
                      + "".join(f"<p class='muted'>{esc(line)}</p>" for line in lines[5:] if not line.startswith("来源："))
                      + "<p>" + "<br>".join(f'<a href="{esc(url)}">查看 Tibo 原帖 / 本人补充</a>' for url in links) + "</p></section>")
    if not events:
        text.append("本次可见记录中没有符合提醒条件的消息。接下来每小时检查；有新公告时发送邮件。")
        blocks.append("<p>本次可见记录中没有符合提醒条件的消息。有新的明确公告时再发送邮件。</p>")
    subject = "Codex 额度重置提醒｜Tibo 更新"
    if first_time and first_time.instant:
        bj, la = first_time.instant.astimezone(BEIJING), first_time.instant.astimezone(PACIFIC)
        suffix = "前" if first_time.deadline else ""
        subject = f"Codex 额度重置提醒｜北京时间 {bj:%m/%d %H:%M}{suffix}｜美国太平洋 {la:%m/%d %H:%M} {la.tzname()}{suffix}"
    elif events and all(event.status == "completed" for event in events):
        subject = "Codex 额度重置提醒｜已完成确认（具体重置时刻未公布）"
    if onboarding:
        subject = "首次汇总｜" + subject
    if demo:
        subject = "[演示] " + subject
    identity = hashlib.sha256(("onboarding" if onboarding else "alert").encode() +
                              "\n".join(sorted(v for e in events for v in e.versions)).encode()).hexdigest()
    title = "邮件格式演示" if demo else "Tibo · Codex 重置提醒"
    body = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tibo Codex 重置提醒</title></head><body style="margin:0;background:#f4f5f1;color:#172725;font-family:Arial,'Microsoft YaHei',sans-serif">
<div style="max-width:640px;margin:32px auto;padding:28px;background:#fff;border:1px solid #dde4db;border-radius:12px">
<div style="color:#527256;font-size:12px;letter-spacing:2px">TIBO RESET MAILER</div>
<h1 style="font-size:24px;margin:12px 0 24px">""" + html.escape(title) + "</h1>" + "".join(blocks) + """
<hr style="border:0;border-top:1px solid #e1e6df;margin:28px 0"><p style="font-size:12px;color:#61706b">公开公告提醒。账户实际可用额度请以产品内显示为准。由你自行部署的 Tibo Reset Mailer 发送。</p>
</div></body></html>"""
    # Inline critical typography for email clients; the preview needs no JS or network assets.
    body = body.replace("class='clock'", "style='font-size:23px;font-weight:bold;line-height:1.6'")
    body = body.replace("class='muted'", "style='font-size:13px;color:#61706b;line-height:1.7'")
    body = body.replace("class='badge'", "style='display:inline-block;background:#edf4e7;color:#355c39;padding:5px 10px;border-radius:5px;font-size:12px'")
    return {"subject": subject, "text": "\n".join(text), "html": body,
            "message_id": f"<{identity[:40]}@tibo-reset-mailer.local>", "date": now.isoformat()}


def send_mail(payload: dict, config: Config):
    config.validate_mail()
    message = EmailMessage()
    message["Subject"] = payload["subject"]
    message["From"] = payload["from"]
    message["To"] = payload["to"]
    message["Date"] = format_datetime(datetime.fromisoformat(payload["date"]))
    message["Message-ID"] = payload["message_id"]
    message.set_content(payload["text"])
    message.add_alternative(payload["html"], subtype="html")
    context = ssl.create_default_context()
    smtp = None
    try:
        if config.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30, context=context)
        else:
            smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config.smtp_username, config.smtp_password)
        try:
            refused = smtp.send_message(message, from_addr=payload["from"], to_addrs=[payload["to"]])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        except (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError):
            raise  # Explicit negative reply: retryable, no acceptance was reported.
        except Exception:
            raise DeliveryUnknown("SMTP outcome uncertain; verify sent mail using the recorded Message-ID") from None
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                smtp.close()  # A failure after confirmed DATA acceptance must not undo success.


def demo_mail():
    published = datetime(2026, 10, 23, 4, 47, 34, tzinfo=timezone.utc)
    post = Post("2113492502355562875", "Landing tomorrow before 10am PST together with a small ship.", published,
                parent_id="2113492187770175603", context_text="Codex quota reset")
    event = Event([post], "scheduled", "此处演示日期与 PST/PDT 歧义的展示方式；这不是新的公告。", post.text, post)
    return build_mail([event], [post], now=published, demo=True)
