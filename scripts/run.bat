@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   PERSO Sales Console - 서버 시작
echo ========================================================
echo.

if not exist .venv (
    echo [오류] .venv가 없습니다. 먼저 scripts\setup.bat 을 실행하세요.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo [시작] FastAPI 서버를 시작합니다...
echo         http://127.0.0.1:8000
echo         종료하려면 Ctrl+C 를 누르세요.
echo.

python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
