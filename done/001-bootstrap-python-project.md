# 001 — Bootstrap Python project

## Goal
Create the Python project scaffold: `pyproject.toml`, virtualenv, base dependencies, ruff/black config, package directories.

## Steps
1. Create `pyproject.toml` with these deps:
   - runtime: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `pydantic>=2`, `pydantic-settings`, `httpx`, `python-dotenv`, `jinja2`, `anthropic` (optional dep)
   - dev: `pytest`, `pytest-asyncio`, `ruff`, `black`, `respx` (for httpx mocks)
2. Add `[tool.ruff]` and `[tool.black]` sections (line-length 100, target-version py311).
3. Make sure `src/` is the package root: add `src/__init__.py` and `src/<each subdir>/__init__.py`.
4. Add `requirements.txt` (derived from `pyproject.toml`) as a convenience.

## Verification
- `python -c "import fastapi, sqlalchemy, pydantic, httpx; print('ok')"` after `pip install -e ".[dev]"`
- `pytest --collect-only` exits 0 with no errors.

## Done when
- `pyproject.toml` exists and installs cleanly.
- `ruff check src/` and `black --check src/` both pass on empty package.
