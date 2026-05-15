@echo off
chcp 65001 > nul
REM 관리자 권한 체크 + 자동 elevation
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [info] 관리자 권한이 필요합니다. UAC 창이 뜨면 '예' 눌러주세요...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ============================================================
echo  덮개 닫을 때 동작 = "아무것도 안 함" 설정
echo ============================================================
echo.

REM 1) LIDACTION 설정 노출 (Windows 가 기본 숨김 처리)
powercfg /attributes SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 -ATTRIB_HIDE

REM 2) AC (전원 연결 상태)
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
echo [AC 전원] 덮개 닫음 -^> 아무것도 안 함

REM 3) DC (배터리 상태)
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS 5ca83367-6e45-459f-a27b-476b1d01c936 0
echo [배터리]  덮개 닫음 -^> 아무것도 안 함

REM 4) 활성 구성표 적용
powercfg /setactive SCHEME_CURRENT

echo.
echo [완료] 이제 노트북 덮개 닫아도 절전/최대절전 안 들어갑니다.
echo.
echo 확인하려면 Win+X -^> 전원 옵션 -^> 추가 전원 설정 -^>
echo 덮개를 닫을 때 작동 -^> "AC/배터리: 아무 작업도 안 함" 인지 보세요.
echo.
pause
