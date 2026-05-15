@echo off
REM ============================================================
REM Ralph Loop - feeds PROMPT.md to claude on every iteration.
REM Stop: create .ralph_stop at repo root, or close the window.
REM
REM IMPORTANT: do NOT redirect stdin from NUL - the claude CLI
REM exits immediately if it cannot detect a real input stream.
REM ============================================================

setlocal EnableExtensions
cd /d "%~dp0\.."

if not exist logs mkdir logs

REM ============================================================
REM 컴퓨터 깨어있음 모드 — Ralph 가 도는 동안 sleep/화면꺼짐 방지.
REM 이 프로세스가 종료되면 자동 해제됨. 별도 창에서 keep_awake.bat
REM 을 따로 띄울 필요 없음.
REM ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "$sig = '[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint flags);'; $t = Add-Type -MemberDefinition $sig -Name P -Namespace W -PassThru; $null = $t::SetThreadExecutionState([uint32]'0x80000003')" 2>nul
echo [keep-awake] 시스템 깨어있음 모드 활성화됨 (sleep/화면보호기 차단).

set ITER=0
if "%SLEEP_BETWEEN%"=="" set SLEEP_BETWEEN=10
if "%MAX_ITER%"=="" set MAX_ITER=0

:loop
set /a ITER+=1
echo.
echo ======================================================
echo  Ralph iteration #%ITER%  ^(%date% %time%^)
echo ======================================================

if exist .ralph_stop (
    echo Stop file detected. Exiting.
    del .ralph_stop
    goto done
)

REM Pipe PROMPT.md into claude as stdin. -p = print mode (one-shot).
type PROMPT.md | claude -p --dangerously-skip-permissions --output-format text 1>> logs\ralph_stdout.log 2>> logs\ralph_stderr.log

set CLAUDE_RC=%ERRORLEVEL%
if not "%CLAUDE_RC%"=="0" (
    echo [iter #%ITER%] claude exited with code %CLAUDE_RC%. See logs\ralph_stderr.log
)

REM Stop if MAX_ITER reached (0 = unlimited).
if not "%MAX_ITER%"=="0" if %ITER% geq %MAX_ITER% (
    echo Reached MAX_ITER=%MAX_ITER%. Exiting.
    goto done
)

echo [iter #%ITER%] sleeping %SLEEP_BETWEEN%s before next iteration...
timeout /t %SLEEP_BETWEEN% /nobreak >nul
goto loop

:done
echo Ralph Loop finished after %ITER% iteration^(s^).
endlocal
