# 077 — agents/common/llm 테스트 커버리지 70% 이상 달성

## Why

Polish mode Check 2: `pytest --cov` 결과 아래 모듈이 70% 미만.

| 모듈 | 현재 커버리지 |
|------|-------------|
| `src/agents/outbound/sources/linkedin_comments.py` | 57% |
| `src/agents/send_worker.py` | 69% |
| `src/common/healthcheck.py` | 60% |
| `src/common/logging.py` | 69% |
| `src/llm/providers/anthropic_api.py` | 35% |

## What to do

1. 각 모듈의 미커버 라인을 확인하고 테스트 추가.
2. 외부 의존성(subprocess, anthropic SDK 등)은 mock 처리.
3. 목표: 각 모듈 70% 이상.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest --cov=src/agents --cov=src/common --cov=src/llm --cov-report=term-missing -q --tb=short
# 위 5개 모듈 모두 >= 70%
```
