# 066 — 메시지 상세 + 발송 버튼 (편집 가능)

## Why

운영자가 LLM 이 만든 초안 확인하고 필요시 편집 후 "보내기" 클릭. 인바운드 / 아웃바운드 / 팔로업 모두 같은 화면.

## What to do

1. `GET /messages/{id}` — 메시지 1건 상세:
   - subject, body (textarea, 편집 가능)
   - meta: category, score, language, channel, to_address, scheduled_at
   - 연결된 contact / prospect 요약
2. `POST /messages/{id}/send` — 편집된 body 받아서 `approve()` 호출 (edited_body 인자) → send_worker 가 처리. 또는 즉시 발송 옵션.
3. `POST /messages/{id}/reject` — 거절 (reason 필드).
4. `POST /messages/{id}/edit` — 저장만 (발송 안 함).
5. HTMX 로 inline 편집 + 결과 응답 (페이지 리로드 없음).

## Acceptance criteria

- 메시지 본문 편집 후 "보내기" 클릭 → DB status 변경 + send_worker 가 처리.
- 거절 시 사유 텍스트 저장.
- 같은 메시지 두 번 발송 못 함 (status 가 `pending_approval` 일 때만 버튼 활성화).

## Verify

브라우저 + curl 로 send/reject 동작 확인.
