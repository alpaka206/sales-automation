@echo off
chcp 65001 > nul
REM ============================================================
REM Ralph Loop 안정 운영 - 모든 잠금/꺼짐 비활성화
REM 관리자 권한 자동 elevation. UAC 창 뜨면 '예' 누르세요.
REM ============================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [info] 관리자 권한이 필요합니다. UAC 창이 뜨면 '예' 누르세요...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo  화면 잠금/꺼짐 모두 비활성화 (Ralph 운영용)
echo ============================================================
echo.

REM 1) 모니터 / 절전 / 최대절전 — AC + DC 모두 사용 안 함
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
echo [1/6] 모니터/절전/최대절전 = 사용 안 함

REM 2) 잠금 후 모니터 끄기 시간도 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO 8EC4B3A5-6868-48c2-BE75-4F3044BE88A7 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO 8EC4B3A5-6868-48c2-BE75-4F3044BE88A7 0
echo [2/6] 잠금 후 모니터 끄기 = 사용 안 함

REM 3) 덮개 닫음 = 아무것도 안 함
powercfg /attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
echo [3/6] 덮개 닫음 = 아무 작업도 안 함

REM 4) 활성 구성표 적용
powercfg /setactive SCHEME_CURRENT

REM 5) 화면 보호기 / 잠금 화면 비활성화 (현재 사용자)
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive /t REG_SZ /d 0 /f >nul
reg add "HKCU\Control Panel\Desktop" /v ScreenSaverIsSecure /t REG_SZ /d 0 /f >nul
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveTimeOut /t REG_SZ /d 0 /f >nul
echo [4/6] 화면 보호기 = 사용 안 함

REM 6) Windows 비활성 자동 잠금 정책 비활성화 (HKLM)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v InactivityTimeoutSecs /t REG_DWORD /d 0 /f >nul
echo [5/6] 비활성 자동 잠금 = 사용 안 함

REM 7) PC 깨어났을 때 비밀번호 요구 끄기
powercfg /setacvalueindex SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>nul
powercfg /setdcvalueindex SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>nul
reg add "HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51" /v ACSettingIndex /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51" /v DCSettingIndex /t REG_DWORD /d 0 /f >nul 2>&1
echo [6/6] 깨어났을 때 비밀번호 요구 = 사용 안 함

REM 즉시 반영
rundll32.exe user32.dll,UpdatePerUserSystemParameters

echo.
echo ============================================================
echo  완료 — 잠금 화면 / 모니터 꺼짐 / 절전 모두 비활성화됨.
echo ============================================================
echo.
echo  되돌리려면:
echo    powercfg /restoredefaultschemes
echo    reg delete "HKCU\Control Panel\Desktop" /v ScreenSaveActive /f
echo.
pause
