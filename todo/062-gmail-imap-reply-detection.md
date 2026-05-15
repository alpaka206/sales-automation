# 062 — Gmail IMAP 답장 감지 (SMTP 발송분용)

## Why

아웃바운드를 Gmail SMTP 로 보낸 경우 HubSpot 으로는 답장 감지 안 됨. IMAP 으로 받은 편지함 폴링해서 In-Reply-To / References 헤더로 매칭.

## What to do

1. `src/integrations/gmail_imap.py` 신규:
   - `IMAPClient(username, password)` — App Password 사용.
   - `fetch_replies(since_dt) -> list[{message_id, in_reply_to, from_addr, subject, body_snippet}]`.
2. `src/agents/reply_check.py` 확장:
   - SMTP 로 보낸 메시지 (`from_address` 가 SMTP_FROM_EMAIL) 는 IMAP 으로 답장 매칭.
   - 매칭 조건: `In-Reply-To` 헤더가 우리 메시지의 Message-ID 거나, `from_addr == message.to_address` AND `created_at > message.sent_at`.
3. 보내는 메일에 `Message-ID` 헤더 명시적으로 부여 (smtp.py 수정) — IMAP 매칭 안정화.
4. `.env.example` 에 `GMAIL_IMAP_USERNAME`, `GMAIL_IMAP_PASSWORD`, `GMAIL_IMAP_FOLDER=INBOX` 추가.

## Acceptance criteria

- 단위 테스트: mock IMAP 응답으로 매칭 검증.
- 메시지에 `In-Reply-To` 가 있으면 정확 매칭, 없으면 from + 시간 윈도우 fallback.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_gmail_imap.py tests/test_reply_check.py -q
```
