# 090 — 코드 품질 정리 (Polish Check 1)

## Why

Polish 모드 Check 1 (코드 품질) 에서 발견된 소소한 이슈들.

## What to do

### 1. 미사용 import 제거
- `src/api/web/routes.py:18` — `Contact` import 제거 (DomainProfile 조회에 직접 사용하지 않음)
- `tests/agents/test_domain_enrichment.py:5` — `timedelta` import 제거
- `tests/integrations/test_web_fetch.py:7` — `pytest` import 제거
- `tests/integrations/test_web_fetch.py:10` — `HomepageMeta` import 제거

### 2. 미사용 변수 정리
- `tests/agents/test_domain_enrichment.py:97` — `result` 변수 할당 후 미사용 → `_ =` 로 변경하거나 assert 추가

### 3. 미사용 함수 정리
- `src/common/domains.py:is_role_address()` — 현재 호출처 없음. 089 todo 명세에서 "별도 신호로 기록"하도록 의도된 함수이므로, 인바운드 _fetch_contact 에서 role address 여부를 contact_info 에 추가하거나, 아직 사용 안 하면 함수는 유지하되 docstring 에 향후 사용 예정임을 명시.

### 4. 조용한 예외 삼킴 수정
- `src/integrations/compliance.py:123-124` — `is_suppressed()` 에서 `except Exception` 이 로깅 없이 `return False`. `logger.warning` 1줄 추가.

## Acceptance criteria

- `ruff check src tests` 에 경고 0건 (기존 경고도 포함).
- 위 4개 카테고리 모두 수정 완료.
- 기존 테스트 회귀 없음.

## Verify

```powershell
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m pytest -q --tb=short
```
