# 083 — 헬스체크 Claude CLI 항목명을 한국어 라벨로 변경

## Why

Polish Check 3.5: 헬스체크에 "Claude CLI 로그인 상태" 항목이 있어야
하는데 현재 `"claude_cli_token"` (영문 기계명)으로 표시됨.

## What to do

1. `src/common/healthcheck.py`의 `_check_claude_cli()` 함수에서
   `CheckResult(name=...)` 값을 `"Claude CLI 로그인 상태"`로 변경.
2. 이 name을 참조하는 코드 모두 업데이트:
   - `src/api/web/routes.py` (settings 페이지에서 Claude CLI 상태 판별)
   - `tests/test_healthcheck_extra.py`
   - 기타 name 매칭 코드

## Verify

```bash
pytest tests/test_healthcheck_extra.py tests/test_settings_page.py -q
```
