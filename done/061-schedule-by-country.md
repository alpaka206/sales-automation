# 061 — 아웃바운드 메시지에 국가별 발송 시간 자동 적용

## Why

[[049]] 의 `country_send_windows` + [[050]] 의 `send_worker` 결합. 아웃바운드 메시지 저장 시 recipient 국가 기준 `scheduled_at` 자동 계산.

## What to do

1. `OutboundAgent._persist_message()` 에서:
   - `candidate.country` 기반으로 `scheduler.compute_next_send_time(country)` 호출.
   - 결과를 `messages.scheduled_at` 에 저장.
   - country 없으면 `default` 윈도우 사용.
2. 인바운드는 즉시 발송 (`scheduled_at = now`) 유지.
3. 사용자 수동 발송 (웹 UI "지금 보내기") 시 `scheduled_at = now` 로 override.

## Acceptance criteria

- 미국 candidate 한국 자정 UTC 에 처리 시 → `scheduled_at` ≈ US 다음 화-목 9-11AM ET.
- 한국 candidate → 다음 평일 9-11 KST.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_outbound_scheduling.py -q
```
