# 045 — 이메일 + WhatsApp 이중 발송 디스패처

## Why

[[044]] 의 senders/__init__.py 와 연계. 인바운드 답장 발송 시 이메일은 무조건, WhatsApp 은 phone 존재 + 게이트 켜져 있으면 시도. 결과를 DB 에 남겨야 함.

## What to do

1. `src/db/migrations/0003_whatsapp_log.py` — `messages` 에 컬럼 추가:
   - `whatsapp_attempted` BOOL DEFAULT false
   - `whatsapp_sent` BOOL DEFAULT false
   - `whatsapp_error` TEXT NULLABLE
2. `senders/__init__.py:send()` 변경:
   - 이메일 성공 후 try-block 으로 WhatsApp 시도.
   - 성공/실패 결과를 위 컬럼에 기록.
3. 다음의 정책 명시 (코드 + 문서):
   - 이메일 실패 → 전체 실패 (예외 raise).
   - 이메일 성공 + WhatsApp 실패 → 메시지 전체는 성공. 단 에러 로그.

## Acceptance criteria

- `messages` 테이블에 WhatsApp 시도 기록 컬럼 3개 존재.
- 단위 테스트: 이메일 성공/WA 실패 케이스, 둘 다 성공 케이스 검증.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_dual_dispatcher.py -q
```
