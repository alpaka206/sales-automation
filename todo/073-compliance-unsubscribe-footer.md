# 073 — Unsubscribe 링크 + 메일 footer + suppression 테이블

## Why

콜드메일은 GDPR / CAN-SPAM / 한국 정통망법 위반 위험. 발송 메일에 unsubscribe 링크 + 발신자 정보 footer 필수. unsubscribe 처리 후 동일 주소 재발송 X.

## What to do

1. `src/db/migrations/0010_email_suppression.py`:
   - `email_suppression` 테이블: `email` (PK, 정규화), `reason` (str: `unsubscribe`/`bounce`/`spam_complaint`), `created_at`.
2. `src/integrations/senders/__init__.py` 의 `send()` 진입 시 suppression 체크. 차단된 주소면 발송 안 함 + `messages.status=suppressed`.
3. 메일 본문 마지막에 footer 자동 append:
   - "이 메일은 perso(devrel.365@gmail.com)의 영업 안내입니다."
   - "구독 해지: https://localhost:8000/unsubscribe?token=..." (또는 cloudflared URL)
   - 한국어 외 언어는 LLM 가 번역해서 가져옴.
4. `GET /unsubscribe?token=...` 라우트 — token 검증 후 suppression 테이블에 추가 + 친절한 응답 페이지.
5. token = HMAC(email + INTERNAL_API_TOKEN) — 추측 방지.

## Acceptance criteria

- 모든 outbound 메일 끝에 footer 자동 부착.
- unsubscribe 링크 클릭 → 해당 이메일 영구 차단.
- 차단된 이메일에 재발송 시도 시 즉시 차단 + 로그.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_suppression.py tests/test_unsubscribe.py -q
```

## Risks

- GDPR 준수는 코드만으로 부족 — privacy policy 페이지 별도 필요 (사용자 작업).
