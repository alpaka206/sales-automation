# 072 — 첫 실행 .env 자동 생성 마법사 + claude CLI 안내

## Why

비개발자가 .exe 실행했을 때 .env 채울 줄 모름. 첫 실행 시 인터랙티브 입력 받아서 자동 채움.

## What to do

1. `src/cli.py` 에 `init` 서브커맨드 추가:
   - `.env` 없으면 인터랙티브 질문 (HubSpot 토큰, Gmail SMTP creds, SMTP_FROM_EMAIL, INTERNAL_API_TOKEN auto-generate, DATABASE_URL).
   - 각 항목별로 "어디서 받는지" 한 줄 설명.
   - 빈 값 허용 (선택 항목).
2. `.exe` 또는 `setup.bat` 첫 실행 시 자동으로 `python -m src.cli init` 호출.
3. claude CLI 미설치 감지 시 "https://docs.anthropic.com/claude-code 가서 설치 + `claude /login`" 안내.

## Acceptance criteria

- `.env` 없는 상태에서 init 실행 시 질문 답하면 .env 생성됨.
- 기존 `.env` 있으면 덮어쓰지 않음 (--force 옵션 별도).

## Verify

```powershell
del .env
.venv\Scripts\python.exe -m src.cli init
type .env   # 값들 채워져 있음
```
