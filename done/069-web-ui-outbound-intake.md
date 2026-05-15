# 069 — 아웃바운드 자연어 입력 폼 + 발굴 결과 검토 화면

## Why

사용자 명세: 웹에 "이런 사람들을 조사해와" 자연어 입력 → BE 가 발굴 → 결과 후보 리스트 → 사용자 사람마다 확인 후 발송 ok.

## What to do

1. `GET /outbound/new` — 텍스트 입력 폼 + 옵션 (max_results, dry_run).
2. `POST /outbound/run-intent` — [[053]] dispatcher 호출 → `outbound_intents` 에 기록 → 비동기 background task 로 발굴 시작 → intent_id 응답.
3. `GET /outbound/intents/{id}` — 발굴 진행 상황 + 결과 (HTMX 폴링으로 자동 갱신).
4. `GET /prospects` — 모든 prospect 목록 (필터: source, country, status, score). 일괄 ok 체크박스.
5. `POST /prospects/bulk-approve` — 선택된 prospect 의 초안 메시지 모두 approved 상태로 → send_worker 가 처리.

## Acceptance criteria

- "성형외과 마케팅 채용 공고" 입력 → 발굴 → 후보 10개 표시.
- 체크박스 다중 선택 → "선택 발송" 한 번에 처리.

## Verify

브라우저 + e2e 테스트.
