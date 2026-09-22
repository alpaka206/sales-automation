"""Run local pytest with disposable storage and no outbound network connections.

Usage: python scripts/check_policy_response.py --result-dir tmp/policy-check tests -q
Only fixtures/mocked providers are evaluated; this is never a live Gemini evaluation.
"""
from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time


def main() -> int:
    original_command = [sys.executable, *sys.argv]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", required=True)
    args, pytest_args = parser.parse_known_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    result_dir = Path(args.result_dir).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)

    # Set both token aliases: the newer alias otherwise wins over the test token.
    os.environ.update({
        "HUBSPOT_ACCESS_TOKEN": "test-hubspot-token",
        "HUBSPOT_PRIVATE_APP_TOKEN": "test-hubspot-token",
        "GOOGLE_CREDENTIALS_JSON": "",
        "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN": "",
        "GOOGLE_OAUTH_CLIENT_SECRET": "",
        "SLACK_BOT_TOKEN": "",
        "SLACK_ENABLED": "false",
        "APPROVAL_CHANNEL": "none",
        "INBOUND_POLL_ENABLED": "false",
        "INBOUND_WORKER_ENABLED": "false",
        "SEND_WORKER_ENABLED": "false",
        "FOLLOWUP_SEQUENCE_SINCE": "",
        # Existing transport tests need the gate open against mocked transports.
        "LIVE_EXTERNAL_WRITES": "true",
        "LIVE_HUBSPOT_WRITES": "true",
        "LIVE_SHEETS_WRITES": "true",
    })

    def deny_network(event, event_args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            # Windows asyncio implements its wakeup socketpair using loopback TCP.
            if event == "socket.connect" and event_args[0].family not in {
                socket.AF_INET, socket.AF_INET6,
            }:
                return
            host = event_args[0] if event == "socket.getaddrinfo" else event_args[-1][0]
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise RuntimeError("External network disabled by local policy-response checks")

    sys.addaudithook(deny_network)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="policy-response-") as temp_dir:
        os.environ["DATABASE_URL"] = "sqlite:///" + Path(temp_dir, "tests.db").as_posix()
        import pytest

        command_args = [*pytest_args, "-p", "no:cacheprovider", f"--junitxml={result_dir / 'junit.xml'}"]
        with (result_dir / "pytest.txt").open("w", encoding="utf-8") as output:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exit_code = int(pytest.main(command_args))
        # Windows cannot remove SQLite files while a pooled connection remains open.
        from src.db.session import engine
        engine.dispose()
    report = {
        "command": original_command,
        "cwd": str(root),
        "status": "PASS" if exit_code == 0 else "FAIL",
        "exit_code": exit_code,
        "duration_seconds": round(time.monotonic() - started, 3),
        "network": "EXTERNAL_DENIED; numeric loopback only for Windows asyncio",
        "database": "disposable SQLite; production URL overridden",
        "live_model_evaluation": "NOT_RUN",
        "pytest_args": command_args,
    }
    (result_dir / "command.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
