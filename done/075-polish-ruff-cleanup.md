# 075 — ruff 린트 경고 정리

## Why

Polish mode Check 1: `ruff check src tests` 에서 27개 오류 발견. 대부분 미사용 import (F401).

## What to do

1. `.venv/Scripts/ruff check src tests --fix` 로 자동 수정 가능한 25개 처리.
2. 나머지 2개 (unsafe fix 필요) 수동 확인 후 처리.
3. `ruff check src tests` 통과 확인.

## Verify

```powershell
.venv\Scripts\ruff check src tests
# 0 errors expected
.venv\Scripts\python.exe -m pytest -q --tb=no
# all pass
```
