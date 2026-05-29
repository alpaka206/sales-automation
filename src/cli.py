"""CLI entry point for sales automation tools."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def doctor() -> int:
    """Run pre-flight checks. Returns 0 if all required checks pass."""
    results: list[tuple[str, str, str]] = []

    from src.common.config import settings

    env_exists = os.path.exists(".env")
    results.append(("PASS" if env_exists else "WARN", ".env file", "exists" if env_exists else "missing - copy .env.example to .env"))

    db_exists = os.path.exists("data/app.db")
    results.append(("PASS" if db_exists else "FAIL", "Database", "data/app.db found" if db_exists else "run: python scripts/init_db.py"))

    if settings.LLM_PROVIDER == "gemini_api":
        has_key = bool(settings.GEMINI_API_KEY)
        results.append(("PASS" if has_key else "FAIL", "Gemini API Key", "set" if has_key else "GEMINI_API_KEY empty"))
    elif settings.LLM_PROVIDER == "claude_cli":
        try:
            res = subprocess.run(
                [settings.CLAUDE_CLI_PATH, "--version"],
                capture_output=True, text=True, timeout=3,
            )
            ok = res.returncode == 0
            results.append(("PASS" if ok else "FAIL", "Claude CLI", res.stdout.strip() if ok else f"exit code {res.returncode}"))
        except FileNotFoundError:
            results.append(("FAIL", "Claude CLI", f"'{settings.CLAUDE_CLI_PATH}' not found on PATH"))
        except subprocess.TimeoutExpired:
            results.append(("WARN", "Claude CLI", "timed out"))
    elif settings.LLM_PROVIDER == "anthropic_api":
        has_key = bool(settings.ANTHROPIC_API_KEY)
        results.append(("PASS" if has_key else "FAIL", "Anthropic API Key", "set" if has_key else "ANTHROPIC_API_KEY empty"))

    hs_token = bool(settings.HUBSPOT_PRIVATE_APP_TOKEN)
    results.append(("PASS" if hs_token else "WARN", "HubSpot Token", "set" if hs_token else "not set (optional)"))

    slack = bool(settings.SLACK_BOT_TOKEN and settings.SLACK_APPROVAL_CHANNEL_ID)
    results.append(("PASS" if slack else "WARN", "Slack", "configured" if slack else "not fully configured (optional)"))

    yt = bool(settings.YOUTUBE_API_KEY)
    results.append(("PASS" if yt else "WARN", "YouTube API", "key set" if yt else "not set (optional)"))

    smtp_ok = settings.EMAIL_PROVIDER != "smtp" or (bool(settings.SMTP_USERNAME) and bool(settings.SMTP_PASSWORD))
    results.append(("PASS" if smtp_ok else "FAIL", "SMTP", "ok" if smtp_ok else "EMAIL_PROVIDER=smtp but credentials missing"))

    icons = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}
    has_fail = False

    print("\n  Sales Automation - Pre-flight Check\n")
    for status, name, detail in results:
        icon = icons[status]
        print(f"  {icon} {name:20s} {detail}")
        if status == "FAIL":
            has_fail = True

    print()
    if has_fail:
        print("  Some required checks failed. Fix them before going live.\n")
        return 1
    else:
        print("  All checks passed (warnings are optional).\n")
        return 0


def healthcheck() -> int:
    """Run live connectivity checks. Returns 0 if all PASS, 1 on any FAIL."""
    from src.common.healthcheck import run_healthchecks

    report = run_healthchecks()

    icons = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}

    print("\n  Sales Automation - Health Check\n")
    for c in report.checks:
        icon = icons.get(c.status, "[??]")
        latency = f" ({c.latency_ms}ms)" if c.latency_ms else ""
        print(f"  {icon} {c.name:25s} {c.detail}{latency}")

    print(f"\n  Overall: {report.overall_status}\n")
    return 1 if report.overall_status == "FAIL" else 0


_INIT_FIELDS = [
    ("GEMINI_API_KEY", "Gemini API 키 (https://aistudio.google.com/apikey 무료 발급)", ""),
    ("HUBSPOT_PRIVATE_APP_TOKEN", "HubSpot Private App 토큰 (HubSpot > 설정 > 통합 > 비공개 앱)", ""),
    ("EMAIL_PROVIDER", "이메일 발송 방식 (hubspot 또는 smtp)", "hubspot"),
    ("SMTP_USERNAME", "SMTP 사용자명 (Gmail 주소, smtp 선택 시)", ""),
    ("SMTP_PASSWORD", "SMTP 비밀번호 (Gmail 앱 비밀번호)", ""),
    ("SMTP_FROM_EMAIL", "발신 이메일 주소", ""),
    ("APPROVAL_CHANNEL", "승인 채널 (slack / teams / none)", "slack"),
    ("SLACK_BOT_TOKEN", "Slack Bot 토큰 (xoxb-..., Slack 선택 시)", ""),
    ("SLACK_APPROVAL_CHANNEL_ID", "Slack 승인 채널 ID (C01234...)", ""),
    ("YOUTUBE_API_KEY", "YouTube Data API 키 (YouTube 소스 사용 시)", ""),
]


def init(force: bool = False) -> int:
    """Interactive .env setup wizard."""
    import secrets

    env_path = os.path.join(os.getcwd(), ".env")
    example_path = os.path.join(os.getcwd(), ".env.example")

    if os.path.exists(env_path) and not force:
        print("  .env 파일이 이미 존재합니다. 덮어쓰려면 --force 옵션을 사용하세요.")
        return 0

    print("\n  Sales Automation - 초기 설정\n")
    print("  필수 항목만 입력합니다. 선택 항목은 Enter 로 건너뛰세요.\n")

    values: dict[str, str] = {}

    for key, desc, default in _INIT_FIELDS:
        prompt = f"  {key}\n    {desc}"
        if default:
            prompt += f" [기본값: {default}]"
        prompt += "\n    > "
        val = input(prompt).strip()
        values[key] = val or default

    values["INTERNAL_API_TOKEN"] = secrets.token_urlsafe(32)
    print(f"\n  INTERNAL_API_TOKEN 자동 생성: {values['INTERNAL_API_TOKEN'][:8]}...")

    # LLM provider reminder (Gemini is the default)
    print()
    if values.get("GEMINI_API_KEY"):
        print("  [OK] Gemini API 키 입력됨 (LLM_PROVIDER=gemini_api).")
    else:
        print("  [!!] Gemini API 키가 비어 있습니다.")
        print("       https://aistudio.google.com/apikey 에서 무료 키를 발급받아 .env 의 GEMINI_API_KEY 에 넣으세요.")
        print("       (또는 .env 에서 LLM_PROVIDER 를 anthropic_api / claude_cli 로 변경 가능)")

    # Write .env from example template
    if os.path.exists(example_path):
        with open(example_path, "r", encoding="utf-8") as f:
            template = f.read()
        for key, val in values.items():
            import re
            template = re.sub(
                rf"^{key}=.*$",
                f"{key}={val}",
                template,
                flags=re.MULTILINE,
            )
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(template)
    else:
        with open(env_path, "w", encoding="utf-8") as f:
            for key, val in values.items():
                f.write(f"{key}={val}\n")

    print(f"\n  .env 파일 생성 완료: {env_path}")
    print("  필요시 .env 를 직접 편집해 추가 설정을 입력하세요.\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="sales", description="Sales automation CLI tools")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Run pre-flight checklist")
    sub.add_parser("healthcheck", help="Run live connectivity checks")
    init_parser = sub.add_parser("init", help="Interactive .env setup wizard")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing .env")

    args = parser.parse_args()
    if args.command == "doctor":
        sys.exit(doctor())
    elif args.command == "healthcheck":
        sys.exit(healthcheck())
    elif args.command == "init":
        sys.exit(init(force=args.force))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
