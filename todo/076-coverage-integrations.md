# 076 — integrations 레이어 테스트 커버리지 70% 이상 달성

## Why

Polish mode Check 2: `pytest --cov` 결과 아래 모듈이 70% 미만.

| 모듈 | 현재 커버리지 |
|------|-------------|
| `src/integrations/hubspot.py` | 57% |
| `src/integrations/senders/__init__.py` | 55% |
| `src/integrations/senders/smtp.py` | 38% |
| `src/integrations/slack.py` | 39% |
| `src/integrations/teams.py` | 57% |
| `src/integrations/youtube.py` | 65% |
| `src/integrations/google_search.py` | 61% |

## What to do

1. 각 모듈의 미커버 라인을 확인하고 테스트 추가.
2. 외부 API 호출은 mock 처리 (httpx, smtplib, requests 등).
3. 목표: 각 모듈 70% 이상.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest --cov=src/integrations --cov-report=term-missing -q --tb=short
# 모든 모듈 >= 70%
```
