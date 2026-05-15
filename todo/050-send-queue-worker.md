# 050 — 발송 큐 워커 (시간대 기반 deferred send)

## Why

[[049]] 의 `compute_next_send_time` 을 실제 발송 흐름에 통합. 메시지에 `scheduled_at` 컬럼 추가하고 워커가 `now >= scheduled_at AND status=approved` 인 메시지 발송.

## What to do

1. `src/db/migrations/0006_message_scheduled_at.py`:
   - `messages.scheduled_at` DATETIME NULLABLE 추가.
   - 인덱스 `(status, scheduled_at)`.
2. 인바운드는 즉시 발송 (scheduled_at = now), 아웃바운드는 recipient 국가 기준 다음 적절 시간으로 채움.
3. `src/agents/send_worker.py` 신규 — 비동기 워커:
   - 60초마다 `status=approved AND scheduled_at <= now()` 인 메시지 조회.
   - `senders.send()` 호출. 성공하면 `status=sent, sent_at=now`. 실패하면 `status=send_failed` + 에러 로그.
4. `src/api/main.py` startup 에 워커 task 등록 (`SEND_WORKER_ENABLED=true` 시).
5. 사용자가 웹에서 "발송" 클릭 → `status=approved` + scheduled_at 채워짐 → 워커가 다음 tick 에 발송.

## Acceptance criteria

- 메시지 status 전이: `pending_approval` → (사용자 클릭) → `approved` → (워커 발송) → `sent`.
- scheduled_at 이 미래면 시간 될 때까지 워커가 건드리지 않음.
- 발송 실패 시 status=`send_failed` + 에러 텍스트.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_send_worker.py -q
```
