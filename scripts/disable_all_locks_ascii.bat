@echo off
REM ============================================================
REM Ralph Loop - disable all lock/sleep/screensaver
REM Auto-elevates via UAC. Click Yes on the UAC prompt.
REM ASCII-only to avoid codepage issues.
REM ============================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [info] Admin rights required. Click Yes on the UAC prompt...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo  Disabling screen lock / monitor off / sleep
echo ============================================================
echo.

REM 1) Monitor / sleep / hibernate timeouts -> never (AC + DC)
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
echo [1/6] monitor/sleep/hibernate = never

REM 2) Console lock display off timeout = 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO 8EC4B3A5-6868-48c2-BE75-4F3044BE88A7 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO 8EC4B3A5-6868-48c2-BE75-4F3044BE88A7 0
echo [2/6] console-lock display off = never

REM 3) Lid close action = do nothing
powercfg /attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
echo [3/6] lid close = do nothing

REM 4) Apply scheme
powercfg /setactive SCHEME_CURRENT

REM 5) Screensaver / lock screen off (current user)
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive /t REG_SZ /d 0 /f >nul
reg add "HKCU\Control Panel\Desktop" /v ScreenSaverIsSecure /t REG_SZ /d 0 /f >nul
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveTimeOut /t REG_SZ /d 0 /f >nul
echo [4/6] screensaver = off

REM 6) Inactivity auto-lock policy off (HKLM)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v InactivityTimeoutSecs /t REG_DWORD /d 0 /f >nul
echo [5/6] inactivity auto-lock = off

REM 7) No password on wake
powercfg /setacvalueindex SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>nul
powercfg /setdcvalueindex SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>nul
reg add "HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51" /v ACSettingIndex /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51" /v DCSettingIndex /t REG_DWORD /d 0 /f >nul 2>&1
echo [6/6] password on wake = off

REM Apply changes immediately
rundll32.exe user32.dll,UpdatePerUserSystemParameters

echo.
echo ============================================================
echo  Done. Screen lock / monitor off / sleep all disabled.
echo ============================================================
echo.
echo  To revert:
echo    powercfg /restoredefaultschemes
echo    reg delete "HKCU\Control Panel\Desktop" /v ScreenSaveActive /f
echo.
pause
