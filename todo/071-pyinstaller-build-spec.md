# 071 — PyInstaller build.spec + Windows .exe 빌드

## Why

비개발자가 .exe 더블클릭 하나로 BE 시작. Spring jar 느낌.

## What to do

1. `build.spec` 작성 (PyInstaller):
   - entry: `src/api/main.py` (FastAPI app via uvicorn programmatic launch)
   - datas: `company_rules/*.md`, `src/llm/prompts/**/*.md`, `src/db/migrations/*.py`, `src/api/web/templates/**/*.html`, `src/api/web/static/**`
   - hiddenimports: uvicorn.workers, sqlalchemy.dialects.postgresql, sqlalchemy.dialects.sqlite, anthropic, psycopg2
   - `--onefile --name sales-automation`
2. `scripts/build_windows.bat` — `pyinstaller build.spec` 실행. 결과 `dist/sales-automation.exe`.
3. PyInstaller 가 `claude` CLI 까지 묶지는 못함 — `docs/배포.md` 에 "claude CLI 별도 설치" 안내 명시.
4. `dist/.env.example` 자동 복사 — 첫 실행 시 `.env` 가 없으면 안내.

## Acceptance criteria

- `dist/sales-automation.exe` 더블클릭 → 콘솔창 + FastAPI 서버 시작.
- 첫 실행 시 `.env` 없으면 친절한 안내 + `.env.example` 복사 옵션.
- 파일 크기 < 100MB.

## Verify

```powershell
scripts\build_windows.bat
dist\sales-automation.exe
# 다른 창에서 curl http://localhost:8000/healthz
```

## Risks

- PyInstaller 빌드는 안티바이러스 false positive 가끔 발생.
- 첫 실행 시 압축 해제 5-10초 느림.
