@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 노션 → 정책/지식 문서 동기화

echo ============================================================
echo  노션 문서를 읽어 DB를 최신 내용으로 갱신합니다.
echo.
echo  [!] 사내망에서는 이 방법이 통하지 않습니다.
echo      이 창은 노션을 읽은 뒤 DB에 써야 하는데, 사내망이 DB 포트를
echo      막고 있어 이 PC는 DB에 닿지 못합니다.
echo.
echo      대신 콘솔에서 하세요 - 훨씬 간단합니다:
echo        1) 노션 페이지에서  ...  -  Export  -  Markdown ^& CSV
echo        2) 콘솔 [이메일 템플릿 - 정책 문서] - [노션 Export 올리기]
echo        3) 받은 zip 선택. 끝.
echo.
echo      이 창은 DB에 직접 닿을 수 있는 환경(사외망, 핫스팟, 서버)에서만
echo      의미가 있습니다. 그래도 진행하려면 아무 키나 누르세요.
echo ============================================================
pause
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] .venv 를 찾지 못했습니다. 프로젝트 폴더에서 실행해야 합니다.
    echo     현재 위치: %CD%
    goto :done
)

REM 인자를 그대로 넘겨 줍니다. 파일 탐색기에서 Export.zip 을 이 배치 파일 위에
REM 끌어다 놓으면 그 zip 을 읽습니다 — 노션 쿠키 없이도 되는 방법입니다.
if "%~1"=="" (
    .venv\Scripts\python.exe scripts\sync_notion_local.py
) else (
    echo  내보낸 파일에서 읽는 중: %~1
    echo.
    .venv\Scripts\python.exe scripts\sync_notion_local.py --export "%~1"
)

if errorlevel 1 (
    echo.
    echo ------------------------------------------------------------
    echo  [!] 실패했습니다. 가장 흔한 원인 두 가지:
    echo.
    echo   1) 노션 로그인 정보가 없음
    echo      .env 파일에 NOTION_TOKEN_V2 를 넣어 주세요.
    echo      (얻는 법은 .env.example 에 적혀 있습니다)
    echo.
    echo   2) 위가 번거롭거나 막힐 때 — 언제나 되는 방법
    echo      노션 페이지에서 ... - Export - Markdown ^& CSV 로 zip 을 받은 뒤,
    echo      그 zip 을 이 배치 파일 아이콘 위로 끌어다 놓으세요.
    echo ------------------------------------------------------------
)

:done
echo.
pause
