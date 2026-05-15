# 078 — db/migrate.py 테스트 커버리지 70% 이상 달성

## Why

Polish mode Check 2: `src/db/migrate.py`가 0% 커버리지 (39 stmts).
유일하게 남은 실질적 모듈 — 나머지(agents/outbound.py 2줄 re-export,
migration DDL 파일들)는 너무 작거나 DB 셋업에서 간접 테스트됨.

## What to do

1. `tests/test_db_migrate.py` 작성.
2. in-memory SQLite 사용, `_ensure_tracker`, `_applied`, `run_migrations`
   테스트.
3. 이미 적용된 migration skip, 신규 migration 적용, `_migrations` 테이블
   트래킹 검증.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest --cov=src.db.migrate --cov-report=term-missing -q --tb=short
# src/db/migrate.py >= 70%
```
