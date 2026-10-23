from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .config import Config, address, load_env
from .mail import demo_mail
from .service import check, dispatch
from .state import State, process_lock


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    # Windows terminals may otherwise use GBK, which cannot represent UTC−7.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Tibo Codex 额度重置邮件提醒（独立运行）")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--env-file", default=".env", help="配置文件路径，默认 .env")
    sub = parser.add_subparsers(dest="command", required=True)
    from .agent import add_arguments
    add_arguments(sub)
    once = sub.add_parser("check", help="检查一次；正式运行将发送符合条件的邮件")
    once.add_argument("--dry-run", action="store_true", help="联网检查和生成内容，不发信、不标记已读")
    sub.add_parser("run", help="持续运行，默认每小时检查")
    preview = sub.add_parser("preview", help="生成脱敏的离线演示邮件")
    preview.add_argument("--output", default="docs/email-preview.html")
    sub.add_parser("doctor", help="检查配置，不联网、不发信")
    sub.add_parser("test-email", help="主动发送一封演示邮件，验证邮箱配置")
    sub.add_parser("status", help="查看抓取与投递状态（不输出邮箱与凭据）")
    resolve = sub.add_parser("resolve", help="人工核对已发送邮箱后处理结果不明的投递")
    resolve.add_argument("delivery_id")
    decision = resolve.add_mutually_exclusive_group(required=True)
    decision.add_argument("--sent", action="store_true")
    decision.add_argument("--retry", action="store_true")
    sub.add_parser("setup", help="在当前目录交互生成 .env")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "agent":
            from .agent import run
            output(run(args))
            return 0
        if args.command == "preview":
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(demo_mail()["html"], encoding="utf-8")
            output({"preview": str(path.resolve()), "sent": False})
            return 0
        if args.command == "setup":
            path = Path(args.env_file)
            if path.exists():
                raise ValueError("配置文件已存在，请直接编辑；不会覆盖")
            recipient = address(input("收件邮箱：").strip())
            sender = address(input("发信账号邮箱：").strip())
            host = input("SMTP 主机（例如 smtp.qq.com）：").strip()
            password = getpass.getpass("SMTP 授权码 / 应用密码（隐藏输入）：")
            values = {"MAIL_TO": recipient, "MAIL_FROM": sender, "SMTP_HOST": host,
                      "SMTP_PORT": "465", "SMTP_SECURITY": "ssl", "SMTP_USERNAME": sender,
                      "SMTP_PASSWORD": password, "CHECK_INTERVAL_SECONDS": "3600", "ANALYSIS_MODE": "rules"}
            if any("\n" in v or "\r" in v for v in values.values()):
                raise ValueError("配置值不能含换行")
            with path.open("x", encoding="utf-8") as stream:
                stream.write("\n".join(k + "=" + v for k, v in values.items()) + "\n")
            if os.name != "nt":
                path.chmod(0o600)
            output({"saved": str(path.resolve()), "next": "python -m tibo_mailer test-email"})
            return 0
        load_env(Path(args.env_file))
        config = Config.from_env()
        if args.command == "doctor":
            config.validate_mail()
            if config.mode == "llm":
                config.validate_llm()
            output({"ok": True, "analysis_mode": config.mode, "interval_seconds": config.interval,
                    "note": "配置格式通过；尚未验证 X、SMTP 或模型联网权限"})
            return 0
        if args.command == "check":
            result = check(config, dry_run=args.dry_run)
            output(result)
            return 0 if result["ok"] else 1
        if args.command == "run":
            config.validate_mail()
            if config.mode == "llm":
                config.validate_llm()
            while True:
                started = time.monotonic()
                try:
                    result = check(config)
                    if not result["ok"] or result.get("warnings"):
                        logging.warning("监测需要检查：请运行 status 查看详情")
                    else:
                        logging.info("检查完成；新版本 %s，符合提醒条件 %s", result["new_versions"], result["events"])
                except Exception as exc:
                    logging.error("本轮失败（%s）；未处理的消息会保留重试", type(exc).__name__)
                time.sleep(max(1, config.interval - (time.monotonic() - started)))
        with process_lock(config.data_dir):
            state = State(config.data_dir)
            try:
                if args.command == "status":
                    output(state.status())
                elif args.command == "resolve":
                    row = next((d for d in state.deliveries() if d["id"] == args.delivery_id), None)
                    if row is None or row["status"] != "unknown":
                        raise ValueError("只能人工处理 unknown 状态的投递")
                    state.update(args.delivery_id, "sent" if args.sent else "pending")
                    output({"resolved": args.delivery_id, "status": "sent" if args.sent else "pending"})
                elif args.command == "test-email":
                    config.validate_mail()
                    payload = demo_mail()
                    identity = "test-" + uuid.uuid4().hex
                    payload.update({"to": config.mail_to, "from": config.mail_from,
                                    "message_id": f"<{identity}@tibo-reset-mailer.local>"})
                    state.reserve(identity, payload, [])
                    dispatch(state, config, only_ids=[identity])
                    row = next(d for d in state.deliveries() if d["id"] == identity)
                    output({"id": identity, "status": row["status"], "message_id": payload["message_id"]})
                    return 0 if row["status"] == "sent" else 1
            finally:
                state.close()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # No raw network/provider exceptions, passwords or email addresses in terminal logs.
        safe = str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + "；运行 status 查看状态，参考故障排查文档"
        logging.error("%s", safe)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
