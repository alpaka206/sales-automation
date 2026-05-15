# 085 — 코드 품질 정리: ruff 경고 + 예외 삼키기 수정

## Why

Polish Check 1에서 발견된 코드 품질 문제 3건.

## What to do

1. `tests/test_compliance_footer.py:5` — 미사용 import `AsyncMock` 제거.
   `ruff check` 통과 필요.

2. `src/integrations/senders/__init__.py:43-44` — `except Exception: pass`
   → `logger.warning(...)` 추가. WhatsApp 연락처 조회 실패 시 로깅 필요.

3. `src/api/web/routes.py` — `prospects_bulk_approve()` 내
   `except Exception: pass` → `logger.warning(...)` 추가.
   일괄 승인 실패 시 로깅 필요.

## Verify

```bash
ruff check src tests
pytest -q --tb=no
```
