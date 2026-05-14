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

    if settings.LLM_PROVIDER == "claude_cli":
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
    elif settings.LLM_PROVIDER == "ollama":
        results.append(("PASS", "Ollama", f"configured at {settings.OLLAMA_HOST}"))

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="sales", description="Sales automation CLI tools")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Run pre-flight checklist")

    args = parser.parse_args()
    if args.command == "doctor":
        sys.exit(doctor())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
