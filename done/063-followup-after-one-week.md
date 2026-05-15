# 063 — 1주 무답 자동 팔로업 큐

## Why

사용자 명세: 발송 1주일 뒤 답장 없으면 팔로업 메일 자동. 단 자동 발송 vs 사용자 검토 정책은 보수적으로 — **1차 팔로업은 큐에 들어가서 사용자 ok 받아야 발송** (사용자가 이후 정책 바꿀 수 있게 env flag).

## What to do

1. `.env.example`:
   - `FOLLOWUP_AFTER_DAYS=7`
   - `FOLLOWUP_AUTO_SEND=false`  (true 면 사람 검토 없이 자동, false 면 검토 큐)
   - `MAX_FOLLOWUPS_PER_PROSPECT=2`
2. `reply_check.py` 의 `_should_followup` 갱신:
   - 임계 7일.
   - 답장 감지된 메시지는 스킵.
   - `MAX_FOLLOWUPS_PER_PROSPECT` 도달 시 스킵.
3. 팔로업 초안 생성 (`outbound/followup.md` 프롬프트 이미 존재) → DB `messages.status=pending_approval` (FOLLOWUP_AUTO_SEND=false) 또는 `approved` (true) 로 저장.
4. 사용자가 ok 클릭하면 [[050]] 의 send_worker 가 처리.

## Acceptance criteria

- 7일 지난 미답 메시지가 정확히 followup 큐에 들어감.
- `FOLLOWUP_AUTO_SEND=true` 면 검토 안 거치고 발송 큐로.
- `MAX_FOLLOWUPS=2` 도달 시 더 이상 안 만듦.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_followup.py -q
```
