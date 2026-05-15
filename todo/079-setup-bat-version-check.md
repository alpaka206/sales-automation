# 079 — setup.bat Python 3.11+ 버전 검증 추가

## Why

Polish mode Check 3: `scripts/setup.bat`가 Python 존재만 확인하고
실제 버전이 3.11 이상인지 검증하지 않음. Python 3.10 이하 사용자가
설치를 진행하면 런타임 오류 발생 가능.

## What to do

1. `setup.bat`에서 `PY_VER` 캡처 후 major.minor 파싱.
2. minor < 11이면 에러 메시지 출력 후 exit /b 1.
3. 한국어 안내: "Python 3.11 이상이 필요합니다. 현재 버전: X.Y"

## Verify

```powershell
scripts\setup.bat
# Python 3.12 환경에서 정상 진행되는지 확인
```
