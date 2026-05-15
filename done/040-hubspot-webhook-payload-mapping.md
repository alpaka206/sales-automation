# 040 — HubSpot 웹훅 실페이로드 매핑 + 라우트 강화

## Why

`src/api/main.py` 의 `/webhook/hubspot/inbound` 는 `{event_type, object_id, occurred_at}` 만 받음. 실제 HubSpot 이 보내는 페이로드는 `[{subscriptionType, objectId, propertyName, propertyValue, occurredAt, eventId, ...}]` 형태의 배열. 매핑이 안 맞아서 실연결 시 400 떨어짐.

## What to do

1. `InboundWebhookBody` 를 HubSpot 실제 페이로드 호환으로 변경. 배열 입력도 수용. 필수: `subscriptionType` (=event type), `objectId` (=contact id), `occurredAt`.
2. `subscriptionType` 매핑:
   - `contact.creation` → `event_type=contact.creation`
   - `contact.propertyChange` + propertyName=`lifecyclestage` → `event_type=lifecycle_change`
   - 기타 무시 (로그만 남기고 200 응답).
3. **서명 검증**: HubSpot 은 헤더 `X-HubSpot-Signature-v3` 로 서명을 보냄. `HUBSPOT_WEBHOOK_SECRET` env 추가, SHA256 HMAC 검증. 검증 실패 시 401.
4. 한 페이로드에 여러 이벤트 들어올 수 있으므로 루프로 각각 `InboundAgent.handle()` 호출. 하나 실패해도 나머지 처리.

## Acceptance criteria

- 실제 HubSpot 페이로드 (`tests/fixtures/hubspot_webhook_*.json` 신규) 로 라우트 호출 시 200, agent 실행 흔적 확인.
- 서명 검증 실패 케이스 401.
- 단일 객체 / 배열 입력 모두 지원.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_inbound_webhook_route.py -q
```

## Risks / open questions

- HubSpot 의 서명 알고리즘 (v3 spec) 정확히 따라야 함. 공식 문서 링크 코드 주석에.
