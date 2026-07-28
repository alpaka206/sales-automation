"""Read-only full export of the HubSpot CRM data this project cares about.

STRICTLY READ-ONLY. Every HubSpot call here is a GET or a search/batch-read POST;
there is no PATCH/PUT/DELETE and no object creation anywhere in this file, so it
cannot alter the CRM and cannot send mail. It is safe to run while the pre-launch
guard (LIVE_EXTERNAL_WRITES=false) is on — that guard blocks writes, and this
script never attempts one.

Why it discovers the schema first: HubSpot rejects a whole request with 400 when
you ask for a property that does not exist on the portal. Custom fields (Plan,
IP Country, user seq, space seq, plan tier, plan seq) have portal-specific
internal names, so we read /crm/v3/properties/{object} and request only what is
actually there. That is also why this lives in a script instead of widening
`src/integrations/hubspot._contact_properties()` — a wrong guess there would
break live inbound processing.

Usage (PowerShell):
    # 1) What fields actually exist? (writes nothing but the schema dump)
    .\\.venv\\Scripts\\python.exe scripts\\hubspot_export.py --schema-only

    # 2) Small sample first, to eyeball the shape
    .\\.venv\\Scripts\\python.exe scripts\\hubspot_export.py --limit 25

    # 3) Everything
    .\\.venv\\Scripts\\python.exe scripts\\hubspot_export.py

Output goes to data/hubspot_export/ — `data/` is gitignored, so a dump of real
customer records can never be committed. Never point this at output/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from src.common.config import settings  # noqa: E402


def _use_os_trust_store() -> None:
    """Trust the OS certificate store as well as certifi's bundle.

    The ESTsoft network runs a TLS-inspecting appliance (ePrism SSL, SOOSAN INT)
    that re-signs HTTPS with a private root. Windows trusts it, but Python ships
    its own certifi bundle that does not, so every HubSpot call fails with
    CERTIFICATE_VERIFY_FAILED. `truststore` makes Python read the same store the
    browser uses, which keeps verification ON — never pass verify=False here.
    Optional: on a normal network (or on Render) certifi already works.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


_use_os_trust_store()

BASE = "https://api.hubapi.com"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "hubspot_export"

# CRM objects to export. Association targets are fetched inline by the list call.
OBJECTS: dict[str, tuple[str, ...]] = {
    "tickets": ("contacts", "companies", "deals"),
    "contacts": ("companies",),
    "companies": (),
    "deals": ("contacts", "companies"),
    "emails": ("contacts",),
}

# HubSpot private apps allow ~100 requests / 10s. Stay well under it.
THROTTLE_SECONDS = 0.12
BATCH_SIZE = 100


class ExportError(RuntimeError):
    pass


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )


def _get(client: httpx.Client, url: str, **params: Any) -> dict:
    resp = client.get(url, params=params or None)
    if resp.status_code != 200:
        raise ExportError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
    time.sleep(THROTTLE_SECONDS)
    return resp.json()


def _post(client: httpx.Client, url: str, body: dict) -> dict:
    resp = client.post(url, json=body)
    if resp.status_code not in (200, 207):
        raise ExportError(f"POST {url} -> {resp.status_code}: {resp.text[:300]}")
    time.sleep(THROTTLE_SECONDS)
    return resp.json()


def discover_properties(client: httpx.Client, obj: str) -> list[dict]:
    """Every property defined on this object type, custom fields included."""
    return _get(client, f"/crm/v3/properties/{obj}").get("results", [])


def list_ids(client: httpx.Client, obj: str, associations: tuple[str, ...], limit: int | None) -> list[dict]:
    """Page through every record, carrying associations (which batch-read omits)."""
    records: list[dict] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": BATCH_SIZE}
        if associations:
            params["associations"] = ",".join(associations)
        if after:
            params["after"] = after
        page = _get(client, f"/crm/v3/objects/{obj}", **params)
        records.extend(page.get("results", []))
        if limit and len(records) >= limit:
            return records[:limit]
        after = page.get("paging", {}).get("next", {}).get("after")
        if not after:
            return records


def batch_read(client: httpx.Client, obj: str, ids: list[str], props: list[str]) -> list[dict]:
    """Fetch full property sets. The property list goes in the BODY, so there is no
    URL-length ceiling — this is why we can ask for every discovered field."""
    out: list[dict] = []
    for i in range(0, len(ids), BATCH_SIZE):
        chunk = ids[i : i + BATCH_SIZE]
        body = {"properties": props, "inputs": [{"id": _id} for _id in chunk]}
        result = _post(client, f"/crm/v3/objects/{obj}/batch/read", body)
        out.extend(result.get("results", []))
        print(f"    {obj}: {min(i + BATCH_SIZE, len(ids))}/{len(ids)}", flush=True)
    return out


def _write(name: str, payload: Any) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    # Plain-ASCII help text: the Windows console is cp949 here, and argparse writes
    # the description straight to stdout, so a non-ASCII dash would raise
    # UnicodeEncodeError on `--help`. The full prose lives in the module docstring.
    parser = argparse.ArgumentParser(
        description="Read-only export of HubSpot CRM objects to data/hubspot_export/.",
    )
    parser.add_argument("--schema-only", action="store_true", help="dump property definitions and stop")
    parser.add_argument("--limit", type=int, default=None, help="max records per object (sampling)")
    parser.add_argument(
        "--objects",
        default=",".join(OBJECTS),
        help=f"comma-separated subset of: {','.join(OBJECTS)}",
    )
    args = parser.parse_args()

    token = settings.HUBSPOT_PRIVATE_APP_TOKEN.strip()
    if not token:
        print(
            "ERROR: HUBSPOT_ACCESS_TOKEN is not set in .env.\n"
            "Get it from HubSpot -> Settings -> Integrations -> Private Apps ->\n"
            "'Perso Dubbing Sales Agent' -> Auth tab.",
            file=sys.stderr,
        )
        return 2

    wanted = [o.strip() for o in args.objects.split(",") if o.strip()]
    unknown = [o for o in wanted if o not in OBJECTS]
    if unknown:
        print(f"ERROR: unknown object(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    schema: dict[str, list[dict]] = {}
    with _client(token) as client:
        print("Discovering property schemas...")
        for obj in wanted:
            try:
                defs = discover_properties(client, obj)
            except ExportError as exc:
                print(f"  {obj}: SKIPPED ({exc})")
                continue
            schema[obj] = [
                {"name": p["name"], "label": p.get("label"), "type": p.get("type")}
                for p in defs
            ]
            custom = [p for p in defs if not p.get("hubspotDefined")]
            print(f"  {obj}: {len(defs)} properties ({len(custom)} custom)")

        path = _write("_schema.json", schema)
        print(f"\nSchema written to {path}")

        if args.schema_only:
            for obj, props in schema.items():
                print(f"\n--- {obj} custom-looking fields ---")
                for p in props:
                    low = p["name"].lower()
                    if any(k in low for k in ("plan", "seq", "country", "tier", "website", "domain")):
                        print(f"    {p['name']:<40} {p.get('label')}")
            return 0

        totals: dict[str, int] = {}
        for obj in wanted:
            if obj not in schema:
                continue
            print(f"\nExporting {obj}...")
            listed = list_ids(client, obj, OBJECTS[obj], args.limit)
            ids = [r["id"] for r in listed]
            if not ids:
                print(f"  {obj}: no records")
                totals[obj] = 0
                _write(f"{obj}.json", [])
                continue
            props = [p["name"] for p in schema[obj]]
            full = batch_read(client, obj, ids, props)
            # Re-attach associations from the list pass (batch-read drops them).
            assoc_by_id = {r["id"]: r.get("associations", {}) for r in listed}
            for rec in full:
                rec["associations"] = assoc_by_id.get(rec["id"], {})
            path = _write(f"{obj}.json", full)
            totals[obj] = len(full)
            print(f"  {obj}: {len(full)} records -> {path}")

    print("\n=== done (read-only; nothing in HubSpot was modified) ===")
    for obj, n in totals.items():
        print(f"  {obj:<12} {n}")
    print(f"\nFiles in {OUT_DIR} (gitignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
