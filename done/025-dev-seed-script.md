# 025 — Dev seed script

## Why

Plan 04 calls for `scripts/seed_dev.py` to pre-populate the DB with sample
data for manual testing. Currently developers must exercise the full
inbound/outbound pipeline just to see data in the DB or test reports.

## What to do

1. Create `scripts/seed_dev.py`:
   - Ensure tables exist (call `Base.metadata.create_all`).
   - Insert 3 contacts with varied scores and domains.
   - Insert 2 prospects (one drafted, one skipped).
   - Insert 4 messages (1 sent, 1 pending_approval, 1 rejected, 1 with reply).
   - Insert 1 conversation linking the above.
   - Print a summary of what was seeded.

2. Make it idempotent: skip inserts if the DB already has rows, or add a
   `--force` flag to truncate-then-seed.

3. Add a `seed` command to `scripts/ralph_loop.bat` or document in README.

## Acceptance criteria

- Running `python scripts/seed_dev.py` populates `data/app.db` with test data.
- Running it twice does not create duplicates (unless `--force`).
- `pytest tests/` still passes.

## Verify

```bash
python scripts/seed_dev.py
python -c "from src.db.session import SessionLocal; s = SessionLocal(); print('contacts:', s.query(__import__('src.db.models', fromlist=['Contact']).Contact).count())"
```
