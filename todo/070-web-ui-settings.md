# 070 — 설정 페이지 (헬스체크 + claude CLI 상태 + 환경변수 마스킹)

## Why

운영자가 시스템 정상 작동 여부 한눈에. claude CLI 로그인 만료되면 큼지막한 빨간 배너.

## What to do

1. `GET /settings`:
   - 헬스체크 결과 표시 (DB / HubSpot / SMTP / Anthropic / Slack 등)
   - claude CLI 로그인 상태 (`claude -p "ping" --output-format text` 실행 후 결과)
   - 모든 환경변수 표시 (값은 마스킹: token 의 앞 4자 + ***).
   - 최근 LLM 사용량 (오늘 / 이번 주 호출 수, `llm_usage` 테이블 기반)
2. `POST /settings/refresh-healthcheck` — 즉시 재실행.
3. claude CLI 로그인 만료 감지 시 화면 상단 "Claude CLI 로그인이 만료됨. 터미널에서 `claude /login` 실행" 빨간 배너 (전 페이지에 표시).

## Acceptance criteria

- 모든 환경변수 (이름만, 값은 마스킹) 표시.
- claude CLI 실패 시 배너 표시.

## Verify

브라우저 + `tests/test_settings_page.py`.
