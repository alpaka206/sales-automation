@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 노션 → 정책/지식 문서 동기화

echo ============================================================
echo  노션 문서를 읽어 DB를 최신 내용으로 갱신합니다.
echo.
echo  어떤 문서를 읽을지는 콘솔의 [이메일 템플릿 - 정책 문서] 화면에서
echo  정합니다. 링크가 바뀌었거나 문서를 늘리고 줄이는 것도 거기서 하세요.
echo  이 창은 거기 등록된 것만 읽어옵니다. 노션에는 아무것도 쓰지 않습니다.
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
