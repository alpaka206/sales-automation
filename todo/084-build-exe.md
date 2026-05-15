# 084 — dist/sales-automation.exe 빌드 및 검증

## Why

Polish Check 3.5: 단일 실행 파일 `dist/sales-automation.exe`가 존재해야
하고, 50MB 이하이며, 실행 시 `/healthz` 응답을 해야 함.
현재 `build.spec`은 있지만 빌드된 exe가 없음.

## What to do

1. PyInstaller가 dev 의존성에 포함되어 있는지 확인 (없으면 추가).
2. `pyinstaller build.spec` 실행하여 `dist/sales-automation.exe` 생성.
3. 파일 크기 50MB 이하 확인.
4. exe 실행 후 `/healthz` 엔드포인트가 200 응답하는지 확인.
5. build.spec에서 불필요한 번들링 제거 등으로 크기 최적화 (필요 시).

## Verify

```powershell
# 빌드
pyinstaller build.spec
# 크기 확인
(Get-Item dist\sales-automation.exe).Length / 1MB
# 실행 + healthz (별도 터미널에서)
# dist\sales-automation.exe
# curl http://127.0.0.1:8000/healthz
```
