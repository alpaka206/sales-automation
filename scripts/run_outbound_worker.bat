@echo off
chcp 65001 > nul
setlocal EnableExtensions
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   아웃바운드 로컬 워커 (발굴 실행기)
echo ========================================================
echo.
echo   이 창은 배포된 웹에서 등록한 "발굴 요청"을 받아
echo   이 PC에서 실제 발굴(크롤링/채점/초안작성)을 실행합니다.
echo.
echo   - 켜두면 30초마다 새 요청을 자동으로 가져와 실행합니다.
echo   - 끄려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
echo.

if not exist .venv (
    echo [오류] .venv가 없습니다. 먼저 scripts\setup.bat 을 실행하세요.
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo [시작] 발굴 워커를 시작합니다... (대기 중에는 조용합니다)
echo.

python scripts\run_outbound_worker.py --interval 30

echo.
echo [종료] 워커가 종료되었습니다.
pause
