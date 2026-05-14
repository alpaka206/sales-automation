# 016 — End-to-end smoke test (no real APIs)

## Goal
A single `tests/test_e2e.py` that wires everything together with stubs and runs the full pipeline.

## Scenario
1. POST /webhook/hubspot/inbound with a fake event id.
2. Inbound agent (with stubbed HubSpot returning a hard-coded contact + 1 inbound email; stubbed LLM returning canned classification/draft) produces 1 message in pending_approval.
3. POST /approve/{id} — stubbed sender succeeds, message status=sent.
4. POST /run/report?kind=daily → markdown contains "Inbound today: 1" and the high-priority section is empty.

## Done when
- `pytest tests/test_e2e.py -q` passes.
- This test runs in CI (add a minimal `.github/workflows/test.yml` that just installs and runs pytest).
