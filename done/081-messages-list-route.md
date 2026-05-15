# 081 — Web UI: /messages 목록 페이지 + smoke test

## Why

Polish Check 3.5: `/messages` 라우트가 존재하지 않음.
현재 `/messages/{message_id}` (상세)만 있고 목록 페이지가 없어
비개발자가 전체 메시지를 확인할 수 없음.

## What to do

1. `src/api/web/routes.py`에 `GET /messages` 라우트 추가.
   - DB에서 최근 메시지 목록 조회 (페이지네이션 선택).
   - Jinja2 템플릿으로 목록 렌더링 (기존 패턴 따를 것).
2. `tests/test_web_ui.py`에 `/messages` 200 응답 smoke test 추가.

## Verify

```bash
pytest tests/test_web_ui.py -q
```
