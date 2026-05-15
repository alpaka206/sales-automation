# 044 — WhatsApp Cloud API 풀 구현 (게이팅)

## Why

사용자 명세: 인바운드 답장 시 이메일 + WhatsApp 이중 발송. 글로벌 영업이라 WhatsApp 필요. 현재 `senders/whatsapp.py` 는 `NotImplementedError` 스텁. Meta key 가 아직 없지만 **코드는 완성** 해두고 key 넣으면 즉시 동작하도록.

## What to do

1. `src/integrations/senders/whatsapp.py` 재작성:
   - `async def check_whatsapp_exists(phone: str) -> bool`: Cloud API `/contacts` 엔드포인트는 deprecated. 대신 발송 시도 후 `error.code` 로 판별 (`131009 = invalid recipient`).
   - `async def send_whatsapp_template(phone: str, template_name: str, language_code: str, params: list[str]) -> str`: 첫 메시지는 정책상 template 필수.
   - `async def send_whatsapp_freeform(phone: str, text: str)`: 24시간 내 응답 받은 후만 가능.
   - 모두 `WHATSAPP_ENABLED=false` 면 `NotImplementedError` 던지지 말고 `WhatsAppDisabled` 예외 던져서 호출측이 조용히 스킵.
2. `src/integrations/senders/__init__.py` 의 `send()` 디스패처에서 메시지 채널이 `email` 이라도 추가로 `contact.phone` 있으면 WhatsApp template 발송 시도 (실패해도 메일은 성공).
3. Template 사전 등록 가이드 — `docs/whatsapp_setup.md` 신규. Meta Business Suite 에서 template 만들고 승인 받는 절차. 기본 template 이름은 `sales_reply_intro` 가정.
4. `.env.example` 의 WhatsApp 섹션에 `WHATSAPP_TEMPLATE_NAME=sales_reply_intro` 추가.

## Acceptance criteria

- `WHATSAPP_ENABLED=false` 일 때 코드 호출 시 조용히 스킵 (로그만 INFO).
- `WHATSAPP_ENABLED=true` + 토큰 + template 이름 셋업 시 실제 API 호출 시도 (테스트는 mock).
- 잘못된 번호일 때 graceful 에러 + DB `messages` 의 `status` 는 영향 없음 (이메일은 별개).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_whatsapp_sender.py -q
```

## Risks

- WhatsApp template 사전 승인 1-3일 소요. 사용자 미리 진행해야 함.
- 첫 메시지 template 외에 freeform 보내면 정책 위반.
