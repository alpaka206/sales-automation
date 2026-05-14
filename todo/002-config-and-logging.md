# 002 — Settings and logging

## Goal
Load env via pydantic-settings, structured logging set up early so every other module can use it.

## Steps
1. `src/common/config.py` — `Settings(BaseSettings)` reading from `.env`. Cover every variable in `.env.example`. Provide sensible defaults so missing values do not crash import.
2. `src/common/logging.py` — `setup_logging()` configures stdlib logging with JSON-ish format. Honor `LOG_LEVEL`.
3. Import `setup_logging()` once in `src/api/main.py` (will be created in later todo) — for now just expose the function.

## Verification
- `python -c "from src.common.config import settings; print(settings.LLM_PROVIDER)"` prints `claude_cli` with default env.
- `pytest tests/test_config.py -q` passes (test that boolean / int env values parse correctly).

## Done when
- Config object validates all known env vars.
- Logging helper available.
- Tests added.
