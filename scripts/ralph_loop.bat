@echo off
REM ============================================================
REM  Ralph Loop — runs claude on PROMPT.md repeatedly.
REM  Stops cleanly with Ctrl+C between iterations.
REM ============================================================

setlocal
cd /d "%~dp0\.."

set ITER=0
set SLEEP=10
set MAX_ITER=0

:loop
set /a ITER+=1
echo.
echo ======================================================
echo  Ralph iteration #%ITER%  (%date% %time%)
echo ======================================================

REM Check if anyone left an explicit stop file
if exist .ralph_stop (
    echo Stop file detected. Exiting.
    del .ralph_stop
    goto done
)

REM Run Claude with the master prompt. --dangerously-skip-permissions is required for an unattended loop.
claude -p "@PROMPT.md" --dangerously-skip-permissions --output-format text >> logs\ralph_stdout.log 2>> logs\ralph_stderr.log

REM Stop after MAX_ITER iterations if set (0 = forever)
if not "%MAX_ITER%"=="0" if %ITER% GEQ %MAX_ITER% goto done

echo Sleeping %SLEEP%s before next iteration...
timeout /t %SLEEP% /nobreak > nul
goto loop

:done
echo Ralph loop finished after %ITER% iterations.
endlocal
