# 042 — 인바운드 메일 본문 실제 fetch 강화

## Why

현재 `InboundAgent._fetch_contact()` 는 event 의 `last_message` 필드를 그대로 신뢰. 실제로는 HubSpot 에서 메일 본문을 별도 호출로 가져와야 함. 폼 제출 / 이메일 수신 / contact 노트 등 케이스별 분기 필요.

## What to do

1. `src/integrations/hubspot.py` 에 메서드 추가:
   - `get_latest_form_submission(contact_id) -> str | None` — Forms API.
   - `get_latest_inbound_email(contact_id) -> str | None` — 가장 최근 inbound 방향 engagement 의 본문.
   - `get_latest_note(contact_id) -> str | None`.
2. `InboundAgent._fetch_contact()` 에서 우선순위로 시도 후 `info["last_message"]` 채움 — 폼 → 인바운드 메일 → 노트 → event payload (마지막 fallback).
3. 어떤 소스에서 왔는지 로그 / `info["inbound_source"]` 에 기록.

## Acceptance criteria

- 폼 제출 이벤트면 폼 답변 텍스트가 `last_message` 에 들어감.
- 이메일 수신 이벤트면 본문이 들어감.
- 어디서도 못 가져오면 빈 문자열 + warning 로그.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_inbound_fetch_body.py -q
```
