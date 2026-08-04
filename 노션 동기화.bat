@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 노션 → 정책/지식 문서 동기화

echo ============================================================
echo  노션 문서를 읽어 DB를 최신 내용으로 갱신합니다.
echo.
echo  이 PC에서 노션을 읽어 서버로 올립니다. DB에 직접 닿지 않으므로
echo  사내망에서도 동작합니다.
echo.
echo  어떤 문서를 읽을지는 콘솔 [이메일 템플릿 - 정책 문서]에서 정합니다.
echo  이 창은 거기 등록된 것만 읽고, 노션에는 아무것도 쓰지 않습니다.
echo.
echo  노션을 읽는 방법 두 가지 - 하나만 되면 됩니다:
echo    1) .env 의 NOTION_TOKEN_V2  (한 번 넣어두면 그냥 더블클릭)
echo    2) 노션 Export zip 을 이 배치 파일 아이콘 위로 끌어다 놓기
echo ============================================================
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
    echo  [!] 실패했습니다. 흔한 원인:
    echo.
    echo   1) 노션 로그인 정보가 없음
    echo      .env 에 NOTION_TOKEN_V2 를 넣거나, 노션에서 받은 Export zip 을
    echo      이 배치 파일 아이콘 위로 끌어다 놓으세요.
    echo.
    echo   2) 서버 주소나 토큰이 없음
    echo      .env 의 PUBLIC_BASE_URL 과 INTERNAL_API_TOKEN 이 서버와
    echo      같은 값이어야 합니다.
    echo.
    echo   3) 등록된 문서가 없음
    echo      콘솔 [이메일 템플릿 - 정책 문서 - 노션 문서 추가] 에서 먼저 등록.
    echo ------------------------------------------------------------
)

:done
echo.
pause
