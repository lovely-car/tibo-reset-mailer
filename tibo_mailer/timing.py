from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
PACIFIC = ZoneInfo("America/Los_Angeles")
TIME_SIGNAL = re.compile(r"\btomorrow\b|\btoday\b|\bin\s+\d+\s+(?:minutes?|hours?|days?)\b|"
                         r"\b(?:every|next|on)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|"
                         r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?::\d{2})?\s*[ap]m\b|\bmidnight\b", re.I)
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(PST|PDT|PT)\b", re.I)
MIDNIGHT = re.compile(r"\bmidnight\s+(PST|PDT|PT)\b", re.I)


@dataclass
class Timing:
    beijing: str
    pacific: str
    deadline: bool = False
    instant: datetime | None = None
    note: str = ""
    alternative: str = ""


def format_time(dt: datetime, zone: ZoneInfo):
    local = dt.astimezone(zone)
    delta = local.utcoffset()
    mins = int(delta.total_seconds() // 60)
    sign = "+" if mins >= 0 else "−"
    offset = f"UTC{sign}{abs(mins)//60}" + (f":{abs(mins)%60:02d}" if abs(mins) % 60 else "")
    label = "北京时间" if zone.key == "Asia/Shanghai" else "洛杉矶"
    abbr = "" if zone.key == "Asia/Shanghai" else local.tzname() + "，"
    return f"{local:%Y-%m-%d %H:%M}（{label}，{abbr}{offset}）"


def convert(quote: str, published: datetime) -> Timing:
    deadline = bool(re.search(r"\b(?:before|by)\b", quote, re.I))
    suffix = "之前" if deadline else ""
    unknown = Timing("具体重置时刻未公布", "具体重置时刻未公布", deadline)
    relative = re.search(r"\bin\s+(\d+)\s+(minutes?|hours?|days?)\b", quote, re.I)
    if relative:
        amount, unit = int(relative[1]), relative[2].lower()
        if amount > 10000:
            return unknown
        dt = published + timedelta(**{"minutes" if unit.startswith("minute") else "hours" if unit.startswith("hour") else "days": amount})
        return Timing(format_time(dt, BEIJING) + suffix, format_time(dt, PACIFIC) + suffix, deadline, dt,
                      "相对时间以原帖发布时间计算。")
    match = CLOCK.search(quote)
    midnight = MIDNIGHT.search(quote)
    if not match and not midnight:
        return unknown
    if midnight and not match:
        hour, minute, abbr = 0, 0, midnight[1].upper()
    else:
        hour, minute, ampm, abbr = int(match[1]), int(match[2] or 0), match[3].lower(), match[4].upper()
        if not 1 <= hour <= 12 or minute > 59:
            return unknown
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    zone = PACIFIC if abbr == "PT" else timezone(timedelta(hours=-8 if abbr == "PST" else -7))
    posted_date = published.astimezone(zone).date()
    explicit = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", quote)
    if explicit:
        try:
            day = datetime.strptime(explicit[1], "%Y-%m-%d").date()
        except ValueError:
            return unknown
    elif re.search(r"\btomorrow\b", quote, re.I):
        day = posted_date + timedelta(days=1)
    elif re.search(r"\btoday\b", quote, re.I):
        day = posted_date
    elif midnight and deadline:
        day = posted_date + timedelta(days=1)
    else:
        unknown.note = "原帖没有可确认的日期，保留原文，不推算日期。"
        return unknown
    naive = datetime(day.year, day.month, day.day, hour, minute)
    dt = naive.replace(tzinfo=zone)
    if abbr == "PT":
        # Reject nonexistent or duplicated local times at a DST transition.
        if dt.astimezone(timezone.utc).astimezone(PACIFIC).replace(tzinfo=None) != naive or dt.utcoffset() != dt.replace(fold=1).utcoffset():
            unknown.note = "当地时间处于夏令时切换歧义或缺失区间，暂不换算。"
            return unknown
    note, alternative = ("midnight 按发帖当晚结束的下一午夜解释。" if midnight and not explicit else ""), ""
    if abbr in {"PST", "PDT"} and dt.astimezone(PACIFIC).tzname() != abbr:
        note += f"按原文 {abbr} 字面偏移换算；该日期洛杉矶实际为 {dt.astimezone(PACIFIC).tzname()}。"
        alt = naive.replace(tzinfo=PACIFIC)
        alternative = f"若作者实际泛指洛杉矶当地时间：{format_time(alt, BEIJING)}{suffix}；{format_time(alt, PACIFIC)}{suffix}。此解释未经确认。"
    return Timing(format_time(dt, BEIJING) + suffix, format_time(dt, PACIFIC) + suffix,
                  deadline, dt, note, alternative)
