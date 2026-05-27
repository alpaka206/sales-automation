# 092 — 테스트 커버리지 70% 미만 모듈 보강

## Why

Polish Check 2에서 `pytest --cov=src --cov-report=term-missing -q` 실행 결과,
아래 모듈이 70% 미만. 목표는 모든 핵심 모듈 ≥ 70%.

## 대상 모듈

| 모듈 | 현재 커버리지 | 목표 |
|------|-------------|------|
| `src/agents/inbound_poller.py` | 50% | ≥ 70% |
| `src/agents/send_worker.py` | 61% | ≥ 70% |
| `src/api/main.py` | 56% | ≥ 70% |
| `src/db/session.py` | 55% | ≥ 70% |

참고: `src/db/migrations/` 의 개별 마이그레이션 파일(0%)과
`src/cli.py`(0%), `src/__main__.py`(0%)는 스크립트/마이그레이션이므로
커버리지 대상에서 제외.

`src/agents/outbound.py`(0%, 2줄)는 re-export 모듈이므로 제외.

## What to do

1. `tests/test_inbound_poller.py` — poll_once, poll_tickets_once 의 주요
   경로 테스트 추가 (HubSpot client mock)
2. `tests/test_send_worker.py` — send loop, approved 메시지 처리, 실패
   재시도 경로 테스트 추가
3. `tests/test_api_main.py` 또는 기존 API 테스트 확장 — healthz, webhook,
   approval 외 나머지 엔드포인트 커버
4. `tests/test_db_session.py` — SessionLocal 생성, get_engine 경로 테스트

## Acceptance criteria

- 위 4개 모듈 모두 `pytest --cov` 에서 ≥ 70% 달성.
- 기존 테스트 회귀 없음.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest --cov=src --cov-report=term-missing -q --tb=short
```
