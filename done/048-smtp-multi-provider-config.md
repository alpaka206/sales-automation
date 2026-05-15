# 048 — SMTP 멀티 제공자 안내 + 예시 (Outlook / Brevo / SendGrid)

## Why

사용자가 Gmail 외에 Outlook / Brevo / SendGrid 같은 무료 한도 큰 제공자 쓸 수 있게 안내. 코드 (`smtp.py`) 는 이미 provider-agnostic 이라 .env 만 바꾸면 됨.

## What to do

1. `.env.example` 의 SMTP 섹션에 4가지 예시 추가 (주석 처리):
   - Gmail (현재)
   - Outlook (`smtp-mail.outlook.com:587`)
   - Brevo (`smtp-relay.brevo.com:587`)
   - SendGrid (`smtp.sendgrid.net:587`)
   각 줄에 무료 한도 코멘트.
2. `docs/설정.md` 의 SMTP 섹션에 표 추가 — 제공자별 무료 한도 / 호스트 / App Password 받는 법 링크.
3. `src/common/healthcheck.py` 의 SMTP 체크가 어떤 제공자 쓰는지 로그에 표시 (`SMTP_HOST` 기준).

## Acceptance criteria

- `.env.example` 에 4개 제공자 주석 예시 보임.
- `docs/설정.md` 에 비교 표 + 각 제공자 setup 링크.
- `healthcheck` 출력에 "Using Brevo SMTP" 같은 식별 메시지.

## Verify

```powershell
notepad .env.example   # 4개 제공자 예시 보임
.venv\Scripts\python.exe -m src.cli healthcheck
```
