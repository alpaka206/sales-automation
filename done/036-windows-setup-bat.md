# 036 — Windows 비개발자용 setup.bat / run.bat

## Why

지금은 사용자가 venv 만들고, `pip install`, `python scripts/init_db.py`,
`.env` 복사 등을 손으로 해야 합니다. 비개발자에겐 진입장벽이 높아
자동화된 batch script가 필요합니다. 한 번 더블클릭으로 설치 완료,
다시 더블클릭으로 서버 실행이 목표.

## What to do

1. `scripts/setup.bat` 작성:
   - Python 3.11+ 설치 여부 확인 (`python --version`). 없으면
     공식 다운로드 페이지 URL을 한국어 안내와 함께 출력하고 exit.
   - 저장소 루트에서 `.venv\` 없으면 `python -m venv .venv` 실행.
   - `.venv\Scripts\activate.bat` 활성화 + `python -m pip install
     --upgrade pip`.
   - `pip install -e .` (또는 `pip install -r requirements.txt` —
     pyproject.toml 구성에 맞춰).
   - `.env` 없으면 `.env.example`을 `.env`로 복사하고
     "값을 채워야 한다"는 안내를 한국어로 출력.
   - `python scripts/init_db.py` 실행.
   - `python -m src.cli doctor`로 사전점검 후 결과 출력.
   - 마지막에 "다음 단계: scripts\\run.bat 실행"를 한국어로 안내.

2. `scripts/run.bat` 작성:
   - `.venv\Scripts\activate.bat` 호출.
   - `python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000`
     또는 `python -m src.cli serve` (이미 존재한다면 그것 사용).
   - 사용자가 Ctrl+C로 종료할 때까지 유지.

3. 모든 batch 파일은 `chcp 65001 > nul` 한 줄을 맨 위에 두어
   콘솔 출력이 한글 깨지지 않도록 함.

## Acceptance criteria

- 빈 폴더에 저장소를 클론한 뒤 `scripts\setup.bat` 더블클릭 한 번으로
  venv + 의존성 + DB가 준비됨.
- `scripts\run.bat` 더블클릭하면 FastAPI가 8000번에서 응답.
- batch에 한글 안내가 깨지지 않고 출력됨.
- 이미 .venv가 존재하면 `setup.bat`은 재생성 없이 의존성만 업데이트.

## Verify

```
rmdir /s /q .venv
del .env
scripts\setup.bat
scripts\run.bat
```
별도 PowerShell에서:
```
curl http://127.0.0.1:8000/healthz
```
