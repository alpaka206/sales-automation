@echo off
chcp 65001 >nul
echo [Sales Automation] Windows .exe 빌드 시작

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다. 먼저 scripts\setup.bat 을 실행하세요.
    pause
    exit /b 1
)

.venv\Scripts\pip install pyinstaller >nul 2>&1
echo [1/3] PyInstaller 설치 완료

echo [2/3] 빌드 중... (1-3분 소요)
.venv\Scripts\pyinstaller build.spec --noconfirm --clean >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [오류] 빌드 실패. 로그를 확인하세요:
    .venv\Scripts\pyinstaller build.spec --noconfirm --clean
    pause
    exit /b 1
)

if not exist "dist\sales-automation.exe" (
    echo [오류] dist\sales-automation.exe 가 생성되지 않았습니다.
    pause
    exit /b 1
)

copy /Y .env.example dist\.env.example >nul 2>&1

for %%F in (dist\sales-automation.exe) do set SIZE=%%~zF
set /a SIZE_MB=%SIZE% / 1048576
echo [3/3] 빌드 완료: dist\sales-automation.exe (%SIZE_MB%MB)
echo.
echo 사용법:
echo   1. dist\ 폴더로 이동
echo   2. .env.example 을 .env 로 복사 후 설정
echo   3. sales-automation.exe 실행
echo   4. 브라우저에서 http://localhost:8000 접속
echo.
echo 주의: LLM 은 Vertex AI 서비스 계정 JSON 필요 (.env 의 GOOGLE_CREDENTIALS_JSON)
pause
