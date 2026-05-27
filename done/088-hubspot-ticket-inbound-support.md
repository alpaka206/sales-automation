# 088 — HubSpot Ticket 객체 인바운드 지원

## Why

현재 인바운드 파이프라인은 HubSpot **Contact** 만 처리한다.
운영 시나리오 중 하나로 "고객 문의를 Ticket 으로 받는다" 가 있는데, 지금
코드는 다음 이유로 Ticket 을 못 본다:

- `src/api/main.py:161` `_HUBSPOT_SUBSCRIPTION_MAP` 에 ticket 관련
  subscriptionType 이 없음 (`contact.creation`, `contact.propertyChange/
  lifecyclestage` 만 매핑).
- `src/agents/inbound_poller.py` 는 `search_contacts_sync(lifecycle=lead)`
  만 호출.
- `src/integrations/hubspot.py` 에 Ticket API 메서드 자체가 없음
  (`grep -r ticket src/` → 0 건).
- `src/agents/inbound.py:_fetch_contact` 의 메시지 본문 fetch 는
  form_submission → inbound_email → note 순서로만 시도하고 ticket 은
  본문 후보에 없음.

## What to do

1. `src/integrations/hubspot.py` 에 Ticket API 메서드 추가:
   - `TicketDTO(BaseModel)`: `id, subject, content, pipeline_stage, priority,
     source_type, created_at, primary_contact_id`.
   - `get_ticket_sync(ticket_id: str) -> TicketDTO` — `GET /crm/v3/objects/
     tickets/{id}?properties=subject,content,hs_pipeline_stage,
     hs_ticket_priority,source_type,createdate`.
   - `get_ticket_primary_contact_sync(ticket_id: str) -> str | None` —
     `GET /crm/v3/objects/tickets/{id}/associations/contacts` → 첫 번째
     contact ID 반환.
   - `search_tickets_sync(created_after: datetime, pipeline_stage: str |
     None = None, limit: int = 100) -> list[TicketDTO]` — `POST
     /crm/v3/objects/tickets/search` (Contact search 와 동일 패턴).

2. `src/api/main.py` 의 webhook 라우팅:
   - `_HUBSPOT_SUBSCRIPTION_MAP` 에 `"ticket.creation": "ticket_created"`
     추가.
   - `_map_hubspot_event` 에 `ticket.propertyChange/hs_pipeline_stage` →
     `"ticket_stage_change"` 매핑 분기 추가.
   - `webhook_hubspot_inbound` 에서 `event_type` 이 ticket 계열이면
     `object_id` 를 ticket_id 로 보고 → `get_ticket_primary_contact_sync`
     로 contact_id 조회 → 그 contact_id 와 ticket_id 둘 다 internal
     dict 에 채워서 agent 에 넘김 (`{"event_type": "ticket_created",
     "object_id": <contact_id>, "ticket_id": <ticket_id>, ...}`).
   - contact 가 연결 안 된 ticket 은 로그 남기고 `status=skipped`.

3. `src/agents/inbound.py:InboundAgent`:
   - `_fetch_contact` 에 ticket 우선 분기 추가: `event.get("ticket_id")`
     가 있으면 `hubspot.get_ticket_sync` 로 `subject + "\n\n" + content`
     를 `last_message` 로 세팅하고 `inbound_source = "ticket"`. 그 외엔
     기존 form/email/note 폴백 유지.
   - `_persist` 의 `Conversation` 생성 시 `hubspot_ticket_id` 기록
     (다음 항목에서 컬럼 추가).

4. `src/db/migrations/0013_conversation_ticket_id.py` 신규:
   - `Conversation` 테이블에 `hubspot_ticket_id VARCHAR(64) NULL` 추가
     (인덱스 포함).
   - `src/db/models.py` 의 `Conversation` 모델에도 동일 필드 추가.

5. `src/agents/inbound_poller.py` 확장:
   - 새 env `INBOUND_POLL_TICKETS` (기본 `false`) 를 `src/common/
     config.py:Settings` 에 추가.
   - `poll_once` 에서 토글이 켜져 있으면 `search_tickets_sync` 도 호출.
   - 처리 이력 마커 kind 를 `inbound_ticket_processed` 로 분리해서
     Contact 처리 이력과 충돌 없게.

6. `scripts/run_inbound_ticket.py` 신규:
   - `run_inbound.py` 와 같은 골격, 인자가 `ticket_id` (positional).
   - `get_ticket_sync` + `get_ticket_primary_contact_sync` 호출 후
     InboundAgent 에 ticket-shape event 주입.
   - `--dry-llm` 동일 지원.

7. `.env.example` 에 `INBOUND_POLL_TICKETS=false` 추가.

8. 테스트:
   - `tests/integrations/test_hubspot_tickets.py` — httpx mock 으로
     ticket fetch / search / association 검증.
   - `tests/agents/test_inbound_ticket.py` — InboundAgent 가 ticket
     이벤트를 받았을 때 `last_message` 가 `subject + content` 로 채워지고
     `Conversation.hubspot_ticket_id` 가 저장되는지.
   - `tests/api/test_webhook_ticket.py` — webhook 으로 `ticket.creation`
     이벤트가 들어왔을 때 contact_id 가 association lookup 으로 정규화
     되고 agent 가 호출되는지 (httpx + InboundAgent mock).

## Acceptance criteria

- HubSpot UI 에서 새 Ticket 을 만들고 `scripts/run_inbound_ticket.py
  <TICKET_ID>` 실행하면 → `messages` 테이블에 `status=pending_approval`
  row 가 생기고, 그 row 의 `conversation.hubspot_ticket_id` 가 채워져
  있다.
- `INBOUND_POLL_TICKETS=true` 로 FastAPI 띄운 상태에서 HubSpot 에 Ticket
  을 새로 만들면 polling interval 안에 자동 픽업 (`Event` 테이블에
  `kind='inbound_ticket_processed'` 마커가 남는다).
- webhook 으로 `subscriptionType=ticket.creation` 페이로드가 들어와도
  서명 검증 통과 후 contact 로 정규화되어 처리된다.
- 기존 Contact 기반 인바운드 동작은 회귀 없음 (기존 테스트 모두 그린).

## Verify

```powershell
# 단위 + 통합 테스트
.venv\Scripts\python.exe -m pytest tests/integrations/test_hubspot_tickets.py tests/agents/test_inbound_ticket.py tests/api/test_webhook_ticket.py -q

# 실연동: HubSpot UI 에서 Ticket 1건 생성 → ID 복사
.venv\Scripts\python.exe scripts\run_inbound_ticket.py <TICKET_ID> --dry-llm
.venv\Scripts\python.exe scripts\run_inbound_ticket.py <TICKET_ID>

# poller 자동 픽업
# .env: INBOUND_POLL_ENABLED=true, INBOUND_POLL_TICKETS=true
.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
# 별도 창에서 HubSpot UI 에 Ticket 생성 → 로그 확인
```

## Risks

- HubSpot Private App scope 에 `tickets` (read) 가 빠져 있을 수 있음 —
  `docs/배포.md` 의 scope 안내에 추가해야 함 (`crm.objects.tickets.read`,
  `crm.objects.tickets.write` 는 status 업데이트 필요 시).
- Ticket 에 연결된 Contact 가 없는 경우 (anonymous form 등) 처리 정책
  필요 — 일단 skip 으로 진행하되 로그 충분히 남기도록.
- Ticket 본문(`content`) 이 HTML 인 경우 plain text 변환 누락 시 LLM
  분류가 깨질 수 있음 — 1차 구현은 raw content 그대로 넣되, 필요시
  후속 todo 로 분리.
