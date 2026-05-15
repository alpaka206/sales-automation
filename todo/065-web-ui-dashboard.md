# 065 — 웹 UI 대시보드 (메시지 + 카운트)

## Why

운영자가 한 화면에서 현황 파악. 최근 메시지 / 상태별 카운트 / 오늘 발송수.

## What to do

1. `GET /` 핸들러 — context:
   - 최근 메시지 20건 (status, category, subject, created_at)
   - status 별 카운트 (`pending_approval`, `approved`, `sent`, `bounced`, `replied`)
   - 오늘 발송수 / 일일 한도 (`DAILY_SEND_LIMIT`)
   - 분류별 카운트 (purchase_inquiry / pricing_question / ...)
2. 템플릿 `dashboard.html` — Tailwind cards. 메시지 목록 클릭 시 `GET /messages/{id}`.
3. HTMX 로 30초마다 폴링 (auto-refresh).

## Acceptance criteria

- 대시보드에 위 6가지 데이터 표시.
- 메시지 클릭 → 상세 페이지로 이동.

## Verify

브라우저 `http://localhost:8000/` 에서 본인이 직접 확인.
