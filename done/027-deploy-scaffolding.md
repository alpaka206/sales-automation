# 027 — Deployment scaffolding (Dockerfile, config templates)

## Why

Plan 07 describes free hosting options (Render, Railway, Fly.io) but there
are no deployment configs in the repo. A Dockerfile, production env template,
and basic deploy docs will make it possible to deploy without re-reading the
plan every time.

## What to do

1. Create `Dockerfile`:
   - Python 3.12 slim base.
   - Install dependencies from `requirements.txt` (or `pyproject.toml`).
   - Copy source, expose port 8000.
   - CMD: `uvicorn src.api.main:app --host 0.0.0.0 --port 8000`.

2. Create `.env.production.example`:
   - Same as `.env.example` but with production-appropriate defaults
     (e.g. LOG_LEVEL=WARNING, real DATABASE_URL placeholder, etc.).

3. Create `render.yaml` (Render blueprint):
   - Web service definition pointing to the Dockerfile.
   - Environment variable references.

4. Add a `deploy/` directory with:
   - `fly.toml` skeleton for Fly.io.
   - `railway.json` skeleton for Railway.

5. Update `README.md` with a "Deployment" section linking to plan/07 and
   the new config files.

## Acceptance criteria

- `docker build -t sales-automation .` succeeds.
- `.env.production.example` exists with all required vars documented.
- At least one PaaS config (render.yaml or fly.toml) is present.
- README links to deployment docs.

## Verify

```bash
docker build -t sales-automation .
pytest tests/ -v -q
```
