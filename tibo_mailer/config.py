from __future__ import annotations

import os
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path: Path) -> None:
    """Read simple KEY=value entries; never execute shell substitutions."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("Invalid .env line: expected KEY=value")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.replace("_", "").isalnum():
            raise ValueError("Invalid .env variable name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def address(value: str) -> str:
    if any(c in value for c in "\r\n"):
        raise ValueError("Email address contains a newline")
    name, parsed = parseaddr(value)
    if name or parsed != value or parsed.count("@") != 1 or " " in parsed:
        raise ValueError("Use one plain email address per field")
    local, domain = parsed.split("@")
    if not local or not domain or "." not in domain:
        raise ValueError("Invalid email address")
    return parsed


@dataclass(frozen=True)
class Config:
    data_dir: Path = Path(".tibo-mailer")
    interval: int = 3600
    mode: str = "rules"
    mail_to: str = ""
    mail_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        e = os.environ
        config = cls(
            data_dir=Path(e.get("TIBO_DATA_DIR", ".tibo-mailer")).expanduser(),
            interval=int(e.get("CHECK_INTERVAL_SECONDS", "3600")),
            mode=e.get("ANALYSIS_MODE", "rules"),
            mail_to=e.get("MAIL_TO", ""), mail_from=e.get("MAIL_FROM", ""),
            smtp_host=e.get("SMTP_HOST", ""), smtp_port=int(e.get("SMTP_PORT", "465")),
            smtp_security=e.get("SMTP_SECURITY", "ssl"),
            smtp_username=e.get("SMTP_USERNAME", ""), smtp_password=e.get("SMTP_PASSWORD", ""),
            llm_base_url=e.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_model=e.get("LLM_MODEL", ""), llm_api_key=e.get("LLM_API_KEY", ""),
        )
        if config.interval < 300:
            raise ValueError("CHECK_INTERVAL_SECONDS must be at least 300")
        if config.mode not in {"rules", "llm"}:
            raise ValueError("ANALYSIS_MODE must be rules or llm")
        return config

    def validate_mail(self):
        address(self.mail_to)
        address(self.mail_from)
        if not all((self.smtp_host, self.smtp_username, self.smtp_password)):
            raise ValueError("Configure SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD")
        if self.smtp_security not in {"ssl", "starttls"} or not 1 <= self.smtp_port <= 65535:
            raise ValueError("Use SMTP_SECURITY=ssl or starttls and a valid port")

    def validate_llm(self):
        parsed = urlsplit(self.llm_base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("LLM_BASE_URL must be HTTPS without embedded credentials")
        if not self.llm_model or not self.llm_api_key:
            raise ValueError("llm mode requires LLM_MODEL and LLM_API_KEY")
