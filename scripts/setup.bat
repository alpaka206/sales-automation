@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   Sales Automation - 초기 설정
echo ========================================================
echo.

REM --- Python 3.11+ 확인 ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo   다음 페이지에서 Python 3.11 이상을 설치하세요:
    echo   https://www.python.org/downloads/
    echo.
    echo   설치 시 "Add Python to PATH" 체크를 반드시 하세요.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [확인] Python %PY_VER% 감지됨

REM --- 버전 3.11 이상 검증 ---
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 goto :py_too_old
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 11 goto :py_too_old
goto :py_ok
:py_too_old
echo.
echo [오류] Python 3.11 이상이 필요합니다. 현재 버전: %PY_VER%
echo.
echo   다음 페이지에서 Python 3.11 이상을 설치하세요:
echo   https://www.python.org/downloads/
echo.
pause
exit /b 1
:py_ok

REM --- venv 생성 또는 기존 사용 ---
if exist .venv (
    echo [확인] .venv 디렉토리가 이미 존재합니다. 기존 환경을 사용합니다.
) else (
    echo [설치] 가상 환경 생성 중...
    python -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상 환경 생성 실패
        pause
        exit /b 1
    )
    echo [완료] .venv 생성 완료
)

REM --- venv 활성화 ---
call .venv\Scripts\activate.bat

REM --- pip 업그레이드 ---
echo [설치] pip 업그레이드 중...
python -m pip install --upgrade pip --quiet

REM --- 의존성 설치 ---
echo [설치] 프로젝트 의존성 설치 중... (1-2분 소요)
pip install -e ".[dev]" --quiet
if errorlevel 1 (
    echo [오류] 의존성 설치 실패. 위 오류 메시지를 확인하세요.
    pause
    exit /b 1
)
echo [완료] 의존성 설치 완료

REM --- .env 파일 ---
if exist .env (
    echo [확인] .env 파일이 이미 존재합니다.
) else (
    echo [설정] .env.example을 .env로 복사합니다.
    copy .env.example .env > nul
    echo.
    echo   ★ 중요: .env 파일을 열어서 필요한 값을 채워 주세요.
    echo     - INTERNAL_API_TOKEN: 반드시 강한 랜덤 값 설정
    echo     - LLM_PROVIDER: claude_cli 또는 anthropic_api
    echo     - 기타 API 키: 사용할 서비스에 맞게 입력
    echo.
)

REM --- DB 초기화 ---
echo [설치] 데이터베이스 초기화 중...
python scripts/init_db.py
if errorlevel 1 (
    echo [오류] DB 초기화 실패
    pause
    exit /b 1
)
echo [완료] 데이터베이스 준비 완료

REM --- 사전점검 ---
echo.
echo --- 사전점검 ---
python -m src.cli doctor

REM --- cloudflared 확인 ---
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo.
    echo [안내] cloudflared 가 설치되어 있지 않습니다.
    echo        HubSpot 웹훅을 로컬에서 받으려면 cloudflared 터널이 필요합니다.
    echo.
    echo        설치: winget install Cloudflare.cloudflared
    echo        설치 후 scripts\tunnel.bat 또는 scripts\run_with_tunnel.bat 을 사용하세요.
) else (
    for /f "tokens=*" %%v in ('cloudflared --version 2^>^&1') do echo [확인] %%v
)

echo.
echo ========================================================
echo   설정 완료!
echo.
echo   다음 단계: scripts\run.bat 을 실행하세요.
echo   (FastAPI 서버가 http://127.0.0.1:8000 에서 시작됩니다)
echo.
echo   외부 접근이 필요하면: scripts\run_with_tunnel.bat
echo ========================================================
echo.
pause
