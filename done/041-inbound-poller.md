# 041 — 인바운드 신규 contact 10분 폴링 워커

## Why

cloudflared 터널이 끊기거나 사용자가 노트북 닫았을 때 웹훅 누락. 폴링 fallback 으로 안정성 보강. 신규 contact 누락 없이 처리.

## What to do

1. `src/agents/inbound_poller.py` 신규. 비동기 워커:
   - 10분마다 (`INBOUND_POLL_INTERVAL_SECONDS=600`) HubSpot `/crm/v3/objects/contacts/search` 호출.
   - 검색 조건: `createdate > last_poll_at` AND `lifecyclestage=lead`.
   - 결과 각각에 대해 `events` 테이블에서 중복 처리 여부 확인 (`kind=inbound_processed`, `payload.contact_id`).
   - 미처리면 `InboundAgent.handle({event_type:"poll", object_id: id, ...})` 호출 후 event 기록.
2. 마지막 폴링 시각은 `events` 테이블의 `kind=inbound_poll_marker` 행으로 추적 (없으면 1시간 전).
3. `src/api/main.py` 의 `@app.on_event("startup")` 에 `asyncio.create_task(run_poller())` 등록. `INBOUND_POLL_ENABLED=true` 일 때만.
4. `.env.example` 에 `INBOUND_POLL_ENABLED`, `INBOUND_POLL_INTERVAL_SECONDS` 추가.

## Acceptance criteria

- BE 띄우면 백그라운드에서 폴링 시작됨 (로그에 "Inbound poller tick" 확인).
- 같은 contact 두 번 처리 안 됨 (events 테이블 중복 방지).
- `INBOUND_POLL_ENABLED=false` 면 시작 안 됨.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_inbound_poller.py -q
```

## Risks

- HubSpot search API rate limit (100/10초). 폴링 주기 10분이면 안전.
