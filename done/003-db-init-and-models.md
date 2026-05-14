# 003 — SQLAlchemy models + init script

## Goal
Implement every table defined in `plan/04_db_schema.md`. Provide `scripts/init_db.py` to create them, and a tiny migration framework.

## Steps
1. `src/db/base.py` — SQLAlchemy `DeclarativeBase`.
2. `src/db/session.py` — engine + `SessionLocal` factory using `settings.DATABASE_URL`.
3. `src/db/models.py` — `Contact`, `Prospect`, `Conversation`, `Message`, `Approval`, `Event` per the spec. Include `created_at` / `updated_at` defaults.
4. `src/db/migrations/` — start with `0001_initial.py` that runs `Base.metadata.create_all(engine)` after first checking the `_migrations` tracker table.
5. `scripts/init_db.py` — runs pending migrations, prints applied ones.

## Verification
- `python scripts/init_db.py` creates `data/app.db` with all tables.
- `python -c "from src.db.session import SessionLocal; from src.db.models import Contact; s=SessionLocal(); s.add(Contact(email='a@b.com', normalized_email='a@b.com', full_name='x')); s.commit(); print(s.query(Contact).count())"` prints 1.
- `pytest tests/test_models.py` covers insert + unique constraint on `hubspot_contact_id`.

## Done when
- DB file created, models all working, tests pass.
