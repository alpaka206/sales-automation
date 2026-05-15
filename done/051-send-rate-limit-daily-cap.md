# 051 — SMTP rate limit + 일일 한도 + jitter

## Why

Gmail/Outlook 등 무료 SMTP 는 분당·일당 한도 있음. 한 번에 burst 발송하면 24시간 정지. 콜드 메일은 분산 발송 필수.

## What to do

1. `.env.example` 에 추가:
   - `SEND_RATE_PER_MINUTE=5` (분당 최대 발송)
   - `DAILY_SEND_LIMIT=100`
   - `SEND_JITTER_SECONDS=15` (메일 사이 0–N초 랜덤 대기)
2. `send_worker.py` (Todo 050) 의 발송 루프:
   - 한 분에 `SEND_RATE_PER_MINUTE` 건 초과 시 다음 분 대기.
   - 일일 카운터 (`events.kind=send_today_count`) 트래킹. `DAILY_SEND_LIMIT` 도달 시 다음날까지 대기.
   - 각 발송 사이 `random.uniform(0, SEND_JITTER_SECONDS)` 초 대기.
3. 사용자가 한도 도달 상태 알 수 있게 헬스체크에 "오늘 X/Y 발송" 표시.

## Acceptance criteria

- 동시에 100건 큐에 들어있어도 분당 5건 이상 안 나감.
- 일일 한도 100 도달하면 그 이후 메시지는 다음 적절 시간으로 미뤄짐.
- 단위 테스트로 rate limiter 검증 (mock 시간).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_send_rate_limit.py -q
```
