@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   Sales Automation - 서버 + 터널 동시 시작
echo ========================================================
echo.

if not exist .venv (
    echo [오류] .venv가 없습니다. 먼저 scripts\setup.bat 을 실행하세요.
    pause
    exit /b 1
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [경고] cloudflared 가 설치되어 있지 않습니다. 서버만 시작합니다.
    echo         터널이 필요하면: winget install Cloudflare.cloudflared
    echo.
    call scripts\run.bat
    exit /b 0
)

echo [1/2] FastAPI 서버를 별도 창에서 시작합니다...
start "Sales Automation - Server" cmd /c "scripts\run.bat"

REM --- 서버가 뜰 때까지 잠시 대기 ---
echo       서버 기동 대기 (3초)...
timeout /t 3 /nobreak > nul

echo [2/2] cloudflared 터널을 시작합니다...
echo.
call scripts\tunnel.bat
