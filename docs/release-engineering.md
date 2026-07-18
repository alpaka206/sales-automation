# Release engineering guide

This document covers the code checks and deployment mechanics that must pass before a release.
It does not replace the integration setup guide.

## Supported runtime

- Python 3.11 and 3.12 are tested in CI.
- Production uses PostgreSQL. SQLite is for one-process local development only.
- The container runs as the unprivileged `app` user and exposes port 8000.

## Dependency policy

`pyproject.toml` is the only dependency source of truth. Local, CI, Docker, and
Render installs use its extras instead of maintaining duplicate requirements files.
When changing a production dependency, update both locations in the same pull request.

Direct dependencies have a tested minimum and a next-major upper bound. This avoids accidental
major upgrades while still accepting security and bug-fix releases on Python 3.11 and 3.12.
CI runs `pip check` and `pip-audit`. A platform-specific transitive lock file is intentionally not
committed because this repository deploys on Linux while developers also use Windows; produce an
environment-specific `pip freeze` only for incident reproduction.

## Local release checks

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
docker build --tag sales-automation:local .
```

## PostgreSQL migrations

Run migrations before starting a new application release:

```powershell
$env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DATABASE"
.\.venv\Scripts\python.exe scripts\init_db.py
```

The migration runner takes a PostgreSQL session-level advisory lock for the whole migration run.
Overlapping deploys therefore wait instead of applying the same schema change concurrently. The
lock is released automatically when the connection closes, including after a process crash.
Running the command a second time must report no pending migrations.

Do not run concurrent SQLite migrations. Back up PostgreSQL before destructive migrations and
roll application code forward when a migration has already changed production data; do not edit
the `_migrations` table manually.

## CI gates

Every push and pull request to `main` must pass:

1. Ruff and the full pytest suite on Python 3.11 and 3.12.
2. `pip check` and a Python 3.12 dependency vulnerability audit.
3. A PostgreSQL 16 service smoke test that applies every migration twice, verifies core tables,
   and checks the application health endpoint.
4. A production Docker image build.

## Deployment checklist

1. Confirm the database backup and `DATABASE_URL` target.
2. Run the exact commit through all CI gates.
3. Apply migrations once; overlapping release jobs are serialized automatically on PostgreSQL.
4. Deploy one application revision and verify `/healthz`.
5. Confirm worker logs and queue counts before enabling customer delivery.
6. Keep the previous image tag available for application rollback.
