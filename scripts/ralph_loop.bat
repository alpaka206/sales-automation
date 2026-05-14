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
