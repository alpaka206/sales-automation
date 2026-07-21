@echo off
chcp 65001 > nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

echo.
echo ========================================================
echo   PERSO Inbound Console - cloudflared 터널
echo ========================================================
echo.

REM --- cloudflared 존재 확인 ---
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [오류] cloudflared 가 설치되어 있지 않습니다.
    echo.
    echo   설치 방법:
    echo     winget install Cloudflare.cloudflared
    echo.
    echo   설치 후 이 스크립트를 다시 실행하세요.
    echo.
    pause
    exit /b 1
)

REM --- data 디렉토리 확인 ---
if not exist data mkdir data

REM --- 임시 로그 파일 ---
set "TUNNEL_LOG=%TEMP%\cf_tunnel_%RANDOM%.log"

echo [시작] cloudflared 터널을 시작합니다 (localhost:8000)...
echo         URL 추출 대기 중...
echo.

REM --- cloudflared 를 백그라운드로 시작, 출력을 로그에 기록 ---
start "" /b cmd /c "cloudflared tunnel --url http://localhost:8000 > "%TUNNEL_LOG%" 2>&1"

REM --- URL 추출 대기 (최대 20초) ---
set FOUND=0
for /L %%i in (1,1,20) do (
    if !FOUND!==0 (
        timeout /t 1 /nobreak > nul
        for /f "usebackq tokens=*" %%a in ("%TUNNEL_LOG%") do (
            echo %%a | findstr /i "trycloudflare.com" > nul 2>&1
            if not errorlevel 1 (
                for /f %%u in ('powershell -NoProfile -Command "$line='%%a'; if ($line -match '(https://[a-zA-Z0-9-]+\.trycloudflare\.com)') { $Matches[1] }"') do (
                    if not "%%u"=="" (
                        set "TUNNEL_URL=%%u"
                        set FOUND=1
                    )
                )
            )
        )
    )
)

if !FOUND!==0 (
    echo [경고] URL 자동 추출에 실패했습니다.
    echo         cloudflared 프로세스가 별도로 실행 중일 수 있습니다.
    echo         로그 파일을 확인하세요: %TUNNEL_LOG%
    echo.
    pause
    exit /b 1
)

REM --- URL 저장 ---
echo !TUNNEL_URL!> data\last_tunnel_url.txt

echo.
echo ========================================================
echo.
echo   터널 URL:
echo.
echo     !TUNNEL_URL!
echo.
echo ========================================================
echo.
echo   data\last_tunnel_url.txt 에 저장 완료.
echo.
echo   HubSpot 웹훅 URL:
echo     !TUNNEL_URL!/webhooks/hubspot
echo.
echo   터널을 종료하려면 Ctrl+C 또는 이 창을 닫으세요.
echo.

REM --- 터널이 계속 실행되도록 유지 ---
:WAIT_LOOP
timeout /t 5 /nobreak > nul
tasklist /fi "imagename eq cloudflared.exe" 2>nul | findstr /i "cloudflared" > nul
if errorlevel 1 (
    echo.
    echo [종료] cloudflared 프로세스가 종료되었습니다.
    pause
    exit /b 0
)
goto WAIT_LOOP
