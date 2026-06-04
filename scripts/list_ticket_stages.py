"""List HubSpot ticket pipeline stages (id + label).

Use the printed stage id for HUBSPOT_TICKET_STAGE_AFTER_SEND (the stage a ticket
moves to once its reply is sent). Reads the token from .env; prints only stage
ids/labels (not the token).

    python scripts/list_ticket_stages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from src.common.config import settings  # noqa: E402


def main() -> None:
    token = settings.HUBSPOT_PRIVATE_APP_TOKEN.strip()
    if not token:
        print("HUBSPOT_PRIVATE_APP_TOKEN is not set in .env")
        return

    resp = httpx.get(
        "https://api.hubapi.com/crm/v3/pipelines/tickets",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        print(f"HubSpot API error {resp.status_code}: {resp.text[:300]}")
        return

    for p in resp.json().get("results", []):
        print(f"\nPipeline: {p.get('label')}  (pipeline id = {p.get('id')})")
        for s in p.get("stages", []):
            print(f"    stage id = {str(s.get('id')):<26} label = {s.get('label')}")
    print("\n→ HUBSPOT_TICKET_STAGE_AFTER_SEND 에 넣을 '발송 후 단계'의 stage id 를 고르세요.")


if __name__ == "__main__":
    main()
