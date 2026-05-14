@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   Sales Automation - Ralph Loop 시작
echo ========================================================
echo.

if not exist .venv (
    echo [오류] .venv가 없습니다. 먼저 scripts\setup.bat 을 실행하세요.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo [시작] Ralph Loop를 시작합니다.
echo         종료하려면 .ralph_stop 파일을 만들거나 창을 닫으세요.
echo.

call scripts\ralph_loop.bat
