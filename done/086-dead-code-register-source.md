# 086 — 미사용 함수 제거: register_source()

## Why

Polish Check 1 (dead code) — `src/agents/outbound/source_registry.py:30`의
`register_source()` 함수가 정의만 되어 있고 어디에서도 호출·임포트되지 않음.

## What to do

1. `src/agents/outbound/source_registry.py`에서 `register_source()` 함수 제거.
2. 소스 등록이 `_SOURCES` dict에 정적으로만 이루어지고 있으므로
   런타임 등록 기능은 불필요.

## Verify

```bash
ruff check src tests
pytest -q --tb=no
```
