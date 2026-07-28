"""Reduce the raw HubSpot dump to the [B2B] AI Dubbing slice that matters.

`hubspot_export.py --objects contacts` faithfully pulls all 538 properties for all
~361k contacts, which lands at ~8 GB — correct, but too big to open or reason about.
Almost all of it is the self-serve product's user base, not B2B sales.

This streams that file (never loads it into memory) and writes a small slice:
contacts associated with a [B2B] AI Dubbing ticket, carrying the fields the Notion
"활용할 허브스팟 고객정보" page asks for. Also reports fill rates, so you can see
which of those fields are actually populated before wiring them into the pipeline.

Read-only against the local dump; it makes no network calls.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\hubspot_b2b_slice.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ijson  # noqa: E402

EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "hubspot_export"
B2B_PIPELINE = "798618015"

# Internal names confirmed to exist on this portal (see _schema.json).
WANTED = [
    "email", "firstname", "lastname", "company", "phone",
    "country", "ip_country", "ip_country_code", "website",
    "plan", "plan_tier", "plan_seq", "user_seq", "space_seq",
    "lifecyclestage", "createdate",
]


def b2b_contact_ids() -> set[str]:
    """Contact ids associated with a [B2B] AI Dubbing ticket."""
    tickets = json.loads((EXPORT_DIR / "tickets.json").read_text(encoding="utf-8"))
    ids: set[str] = set()
    b2b = 0
    for t in tickets:
        if t.get("properties", {}).get("hs_pipeline") != B2B_PIPELINE:
            continue
        b2b += 1
        for assoc in t.get("associations", {}).get("contacts", {}).get("results", []):
            if assoc.get("id"):
                ids.add(str(assoc["id"]))
    print(f"[B2B] tickets: {b2b}, associated contacts: {len(ids)}")
    return ids


def main() -> int:
    src = EXPORT_DIR / "contacts.json"
    if not src.exists():
        print(f"ERROR: {src} not found — run hubspot_export.py first.", file=sys.stderr)
        return 2

    wanted_ids = b2b_contact_ids()
    if not wanted_ids:
        print("No B2B-associated contacts found.", file=sys.stderr)
        return 1

    out: list[dict] = []
    filled = dict.fromkeys(WANTED, 0)
    scanned = 0

    print(f"Streaming {src.name} ({src.stat().st_size / 1024**3:.1f} GB)...")
    with src.open("rb") as fh:
        for rec in ijson.items(fh, "item"):
            scanned += 1
            if scanned % 50000 == 0:
                print(f"  scanned {scanned}...", flush=True)
            if str(rec.get("id")) not in wanted_ids:
                continue
            props = rec.get("properties", {}) or {}
            row = {"id": rec.get("id")}
            for key in WANTED:
                value = props.get(key)
                row[key] = value
                if value not in (None, ""):
                    filled[key] += 1
            out.append(row)

    dst = EXPORT_DIR / "contacts_b2b.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nscanned {scanned} contacts, matched {len(out)}")
    print(f"wrote {dst}  ({dst.stat().st_size / 1024:.0f} KB)\n")
    print(f"{'field':<20} {'filled':>8}  {'of ' + str(len(out)):>10}")
    print("-" * 42)
    for key in WANTED:
        pct = (filled[key] * 100 // len(out)) if out else 0
        print(f"{key:<20} {filled[key]:>8}  {pct:>9}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
