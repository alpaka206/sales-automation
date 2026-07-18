"""Small operator CLI for configuration and live health checks."""

from __future__ import annotations

import argparse


def doctor() -> int:
    """Check the minimum settings required to run the inbound service."""
    from src.common.config import settings

    rows = [
        (bool(settings.DATABASE_URL.strip()), ".env / Database", "DATABASE_URL"),
        (
            bool(settings.GOOGLE_CREDENTIALS_JSON.strip()),
            "Gemini (Vertex)",
            "GOOGLE_CREDENTIALS_JSON",
        ),
        (
            bool(settings.SMTP_USERNAME and settings.SMTP_PASSWORD),
            "SMTP",
            "SMTP_USERNAME / SMTP_PASSWORD",
        ),
    ]

    print("\n  PERSO Inbound Console - Pre-flight Check\n")
    failed = False
    for configured, name, key in rows:
        failed |= not configured
        status = "configured" if configured else f"{key} missing"
        print(f"  {'[OK]' if configured else '[XX]'} {name:20s} {status}")

    optional = {
        "HubSpot": bool(settings.HUBSPOT_PRIVATE_APP_TOKEN),
        "Slack reply-ready alert": bool(
            settings.SLACK_ENABLED
            and settings.APPROVAL_CHANNEL == "slack"
            and settings.SLACK_BOT_TOKEN
            and settings.SLACK_APPROVAL_CHANNEL_ID
        ),
    }
    for name, enabled in optional.items():
        status = "enabled" if enabled else "disabled (optional)"
        print(f"  {'[OK]' if enabled else '[!!]'} {name:20s} {status}")

    print()
    return int(failed)


def healthcheck() -> int:
    """Run live connectivity checks."""
    from src.common.healthcheck import run_healthchecks

    report = run_healthchecks()
    icons = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}
    print("\n  PERSO Inbound Console - Health Check\n")
    for check in report.checks:
        latency = f" ({check.latency_ms}ms)" if check.latency_ms else ""
        print(f"  {icons.get(check.status, '[??]')} {check.name:25s} {check.detail}{latency}")
    print(f"\n  Overall: {report.overall_status}\n")
    return int(report.overall_status == "FAIL")


def main() -> None:
    parser = argparse.ArgumentParser(prog="sales", description="PERSO Inbound Console tools")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="Run pre-flight checklist")
    commands.add_parser("healthcheck", help="Run live connectivity checks")
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "healthcheck":
        raise SystemExit(healthcheck())
    parser.print_help()


if __name__ == "__main__":
    main()
