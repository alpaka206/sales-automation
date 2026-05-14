# 015 — Export starter n8n workflows

## Goal
Hand-craft JSON files for the 6 workflows listed in `plan/06_n8n_workflows.md` and commit them. These are templates — user will adjust credentials after import.

## Steps
1. Create each `n8n_workflows/0X_*.json` as a minimal valid n8n workflow.
2. Use `{{ $env.BE_BASE_URL }}` placeholders in HTTP Request nodes.
3. Add a `README.md` in `n8n_workflows/` explaining import + credentials setup.

## Verification
- Each JSON is valid (json.loads succeeds, top-level `nodes` and `connections` keys present).
- `tests/test_n8n_exports.py` validates the JSON shape minimally.

## Done when
- 6 workflow files committed, all parse, README explains the steps.
