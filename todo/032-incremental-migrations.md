# 032 — Incremental DB migration for llm_usage table

## Why

The `LLMUsage` data currently lives in a JSONL file (`data/llm_usage.jsonl`).
For production, querying usage by date range from a flat file is fragile.
Adding an `llm_usage` table via an incremental migration makes usage data
queryable and consistent with the rest of the system.

## What to do

1. Add `LLMUsage` model to `src/db/models.py`:
   - `id`, `provider`, `model`, `input_tokens`, `output_tokens`,
     `estimated_cost`, `created_at`.

2. Create `src/db/migrations/0002_llm_usage.py` with `up()`:
   - `CREATE TABLE IF NOT EXISTS llm_usage (...)`.

3. Update `src/llm/pricing.py`:
   - `log_usage()` writes to the DB table instead of (or in addition to)
     the JSONL file.
   - `get_usage_since()` queries the DB table.

4. Update report agent's `_llm_cost_summary()` to use the DB-backed query.

## Acceptance criteria

- `python scripts/init_db.py` applies the new migration.
- LLM calls log usage to the `llm_usage` table.
- Report cost section reads from the DB.
- Existing tests pass.

## Verify

```bash
python scripts/init_db.py
pytest tests/ -v -q
```
