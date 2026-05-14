# 024 — Shared test fixtures in conftest.py

## Why

DB session fixtures and mock LLM setup are duplicated across 8+ test files.
A `tests/conftest.py` with shared fixtures will reduce boilerplate and make
test maintenance easier.

## What to do

1. Create `tests/conftest.py` with shared fixtures:
   - `db_engine` — in-memory SQLite engine with tables created.
   - `db_session` — session bound to the engine, auto-rolled-back after each test.
   - `mock_llm` — a MagicMock LLMClient that returns sensible defaults.

2. Refactor existing test files to use the shared fixtures:
   - `test_inbound_flow.py` — remove local `db_session` fixture.
   - `test_approval.py` — remove local `db_with_message` or adapt it.
   - `test_report.py` — remove local `seeded_db` fixture.
   - `test_reply_check.py` — remove local DB setup.
   - `test_outbound_flow.py` — remove local setup.

3. Keep test-specific seed data as local helpers, not in conftest.

## Acceptance criteria

- `tests/conftest.py` exists and is used by at least 3 test files.
- All existing tests still pass.
- No fixtures are duplicated across test files.

## Verify

```bash
pytest tests/ -v -q
```
