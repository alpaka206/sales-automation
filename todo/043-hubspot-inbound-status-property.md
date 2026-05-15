# 043 — HubSpot custom property `inbound_status` + 발송 후 자동 갱신

## Why

사용자 명세: 답장 발송 후 contact 상태가 `new` → `meeting link sent` 로 자동 변경되어야 함. HubSpot 의 어느 필드를 쓸지 결정 + 코드 통합 필요.

## What to do

1. **HubSpot 측 수동 작업 가이드** — `docs/배포.md` 에 섹션 추가:
   - Settings → 객체 → Contacts → 속성 → 새 속성 만들기
   - 이름: `inbound_status`, 내부명: `inbound_status`, 타입: 드롭다운
   - 값: `new` / `analyzed` / `meeting_link_sent` / `replied` / `lost`
2. `src/integrations/hubspot.py`:
   - `update_inbound_status(contact_id, status: str)` 메서드 추가 (실은 `update_contact` 의 wrapper).
3. `src/api/main.py` 의 `/approve/{message_id}` 핸들러:
   - 발송 성공 후 `update_inbound_status(contact_id, "meeting_link_sent")` 호출.
   - 실패해도 발송은 성공 처리, status 갱신은 retry 큐 (events 테이블에 `kind=hubspot_status_update_failed` 로 기록).
4. `InboundAgent.handle()` 끝에 분류 직후 `update_inbound_status(contact_id, "analyzed")` 호출.

## Acceptance criteria

- 인바운드 처리 직후 HubSpot contact 의 `inbound_status` = `analyzed`.
- 답장 발송 후 `meeting_link_sent`.
- 실패 시 retry 큐에 기록 + 다음 폴링에서 재시도.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_hubspot_inbound_status.py -q
```

## Risks

- 사용자가 HubSpot 측에 property 만들기 전에 코드 호출하면 400 떨어짐. `update_contact` 의 응답 코드 보고 친절한 로그 메시지.
