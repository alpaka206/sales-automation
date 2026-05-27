# 091 — 기존 실패 테스트 23건 수정

## Why

Polish Check 1에서 `pytest -q --tb=no` 실행 시 23건의 테스트 실패 발견.
모두 인증/서명 검증 관련 — approval endpoint가 403, webhook route가 401 반환.
이전 커밋에서 인증 로직 변경 시 테스트가 갱신되지 않은 것으로 추정.

## What to do

### 1. Approval endpoint 테스트 7건 (tests/test_approval_endpoint.py)

모든 테스트가 200/400/500 기대하지만 403 반환. 원인 분석:
- `src/api/main.py`의 `/approve/{message_id}` 엔드포인트에 인증 미들웨어/가드가
  추가되었는지 확인
- 테스트에서 올바른 인증 헤더/토큰을 전송하도록 수정

실패 테스트:
- `test_approve_sets_status_to_sent`
- `test_edit_updates_body_then_sends`
- `test_reject_sets_status_rejected`
- `test_invalid_message_returns_400`
- `test_double_approve_returns_400`
- `test_send_failure_returns_500`
- `test_hubspot_logging_failure_still_returns_sent`

### 2. HubSpot inbound status 테스트 4건 (tests/test_hubspot_inbound_status.py)

- `test_handle_sets_analyzed_status` — `update_inbound_status_sync` 호출 안 됨
- `test_handle_continues_on_status_update_failure`
- `test_approve_sets_meeting_link_sent`
- `test_approve_queues_retry_on_status_failure`

mock 설정이 현재 코드 구조와 불일치. 에이전트 코드와 mock 경로 대조 후 수정.

### 3. Webhook route 테스트 12건 (tests/test_inbound_webhook_route.py)

모든 테스트가 200 기대하지만 401 반환. 원인:
- HubSpot webhook 서명 검증이 추가/변경됨
- `_auth_headers()` 헬퍼가 올바른 서명 헤더를 생성하지 않거나
  `HUBSPOT_WEBHOOK_SECRET` 설정이 테스트 환경에서 올바르게 모킹되지 않음

실패 테스트:
- `test_contact_creation_payload`
- `test_lifecycle_change_payload`
- `test_multi_event_payload`
- `test_single_object_not_array`
- `test_legacy_internal_format`
- `test_legacy_format_in_array`
- `test_ignored_subscription_type`
- `test_property_change_non_lifecycle_ignored`
- `test_one_event_error_does_not_block_others`
- `test_invalid_json_returns_400`
- `test_valid_signature_accepted`
- `test_no_secret_configured_skips_verification`

## Acceptance criteria

- `pytest -q --tb=no` 에서 실패 0건 (경고는 허용).
- 기존 통과 테스트 550건 회귀 없음.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest -q --tb=short
```
