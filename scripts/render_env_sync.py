r"""Wipe-and-replace a Render service's environment variables from the local .env.

Render exposes a full-replace endpoint (PUT /v1/services/{id}/env-vars): whatever
list you send becomes the service's ENTIRE env var set, so anything currently on
Render that is NOT in this payload gets removed. That's the "싹 지우고 대체" the
operator wants.

Secrets never touch this chat: the script reads them from your local .env and
sends them straight to Render over HTTPS. It masks values in its own output.

Usage (PowerShell):
    $env:RENDER_API_KEY   = "rnd_..."        # Render dashboard -> Account -> API Keys
    $env:RENDER_SERVICE_ID = "srv-..."        # from the service URL: dashboard.render.com/web/srv-XXXX
    # 1) See the diff (nothing is changed):
    .\.venv\Scripts\python.exe scripts\render_env_sync.py
    # 2) Apply it:
    .\.venv\Scripts\python.exe scripts\render_env_sync.py --apply

Production overrides (differ from local .env) are applied automatically:
    APP_HOST = 0.0.0.0      (bind all interfaces on Render)
    AUTH_MODE = google_oauth (public console uses Google sign-in, not localhost)
LIVE_EXTERNAL_WRITES and SEND_OVERRIDE_EMAIL are sent AS-IS from .env, so the
pre-launch safe mode (blocked writes, mail forced to ronald@…) also holds on Render
until you flip them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.tls import use_os_trust_store  # noqa: E402

# api.render.com is re-signed by the office TLS proxy like every other host, so
# without this the only way through was --insecure, which turns verification off
# entirely while shipping every production secret.
use_os_trust_store()

API = "https://api.render.com/v1"

# Values that must differ on the deployed service vs local dev, plus Render infra
# vars that live in render.yaml (not .env) and must not be wiped.
PROD_OVERRIDES = {
    "APP_HOST": "0.0.0.0",
    "AUTH_MODE": "google_oauth",
    "PYTHON_VERSION": "3.11.9",  # Render build Python; from render.yaml, not .env
    # Local .env leaves this blank so the OAuth callback resolves to 127.0.0.1. A
    # full replace would therefore blank it on Render too, breaking approval links
    # and the Google redirect — the one value that must never be copied from dev.
    "PUBLIC_BASE_URL": "https://sales-automation-4if2.onrender.com",
}

# Never ship these to Render (deploy-tooling creds that live in .env for convenience).
EXCLUDE: set[str] = {"RENDER_API_KEY", "RENDER_SERVICE_ID"}

# Substrings that mark a key as secret, for masking this script's own output.
_SECRET_HINTS = ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIALS", "DATABASE_URL")


def _mask(key: str, value: str) -> str:
    if any(h in key.upper() for h in _SECRET_HINTS) and value:
        return f"{value[:4]}...{value[-2:]} ({len(value)} chars)" if len(value) > 8 else "***"
    return value


def _env_path() -> Path:
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found.")
    return p


def _desired_env() -> dict[str, str]:
    raw = dotenv_values(_env_path())
    env = {}
    for k, v in raw.items():
        if v is None or k in EXCLUDE:
            continue
        # python-dotenv mis-parses `KEY=   # comment` (empty value + inline comment)
        # as the comment being the value. A real value never starts with '#'.
        env[k] = "" if v.strip().startswith("#") else v
    env.update(PROD_OVERRIDES)
    return env


def _render_creds() -> tuple[str, str]:
    """RENDER_API_KEY / RENDER_SERVICE_ID from the shell env, falling back to .env."""
    dotenv = dotenv_values(_env_path())
    api_key = (os.environ.get("RENDER_API_KEY") or dotenv.get("RENDER_API_KEY") or "").strip()
    service_id = (os.environ.get("RENDER_SERVICE_ID") or dotenv.get("RENDER_SERVICE_ID") or "").strip()
    return api_key, service_id


def _fetch_current(client: httpx.Client, service_id: str) -> dict[str, str]:
    """GET the service's current env vars, tolerating both known response shapes."""
    current: dict[str, str] = {}
    cursor: str | None = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = client.get(f"{API}/services/{service_id}/env-vars", params=params)
        r.raise_for_status()
        items = r.json()
        if not items:
            break
        for item in items:
            ev = item.get("envVar", item)  # some responses wrap in {"envVar": {...}}
            if "key" in ev:
                current[ev["key"]] = ev.get("value", "")
        cursor = items[-1].get("cursor")
        if not cursor or len(items) < 100:
            break
    return current


def _verify(insecure: bool):
    """TLS verification: a CA bundle path (corp proxy), or False when --insecure."""
    if insecure:
        print("WARNING: TLS verification disabled (--insecure). Only use on a trusted network.\n")
        return False
    return os.environ.get("RENDER_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True


def main() -> None:
    args = sys.argv[1:]
    apply = "--apply" in args
    api_key, service_id = _render_creds()
    if not api_key or not service_id:
        sys.exit("ERROR: set RENDER_API_KEY and RENDER_SERVICE_ID (shell env or .env) first.")

    desired = _desired_env()

    with httpx.Client(
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=30,
        verify=_verify("--insecure" in args),
    ) as client:
        current = _fetch_current(client, service_id)

        added = sorted(k for k in desired if k not in current)
        removed = sorted(k for k in current if k not in desired)
        changed = sorted(k for k in desired if k in current and current[k] != desired[k])
        unchanged = sorted(k for k in desired if k in current and current[k] == desired[k])

        print(f"Render service: {service_id}")
        print(f"  {len(desired)} vars in .env  vs  {len(current)} currently on Render\n")
        for k in added:
            print(f"  + ADD     {k} = {_mask(k, desired[k])}")
        for k in changed:
            print(f"  ~ CHANGE  {k} = {_mask(k, desired[k])}")
        for k in removed:
            print(f"  - REMOVE  {k}  (on Render, not in .env -- will be wiped)")
        print(f"\n  {len(unchanged)} unchanged.")

        if not apply:
            print("\nDRY RUN. Re-run with --apply to replace the Render env vars.")
            return

        payload = [{"key": k, "value": v} for k, v in desired.items()]
        r = client.put(f"{API}/services/{service_id}/env-vars", json=payload)
        r.raise_for_status()
        print(f"\nApplied. Render replaced the env vars ({len(payload)} total).")
        print("A redeploy is triggered automatically if autoDeploy is on.")


if __name__ == "__main__":
    main()
