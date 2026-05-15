# 067 — knowledge_base CRUD 웹 UI

## Why

[[046]] [[047]] 로 KB 가 DB 에 들어갔으니 웹에서 편집. 비개발자가 요금제·정책·FAQ 를 직접 추가/수정.

## What to do

1. `GET /knowledge` — 문서 목록 (title, categories, scope, updated_at).
2. `GET /knowledge/new` — 새 문서 폼 (title, categories multiselect, scope select, body markdown textarea).
3. `GET /knowledge/{id}` — 상세 + 편집 폼.
4. `POST /knowledge` / `PUT /knowledge/{id}` / `DELETE /knowledge/{id}` — CRUD.
5. 본문 입력 시 미리보기 (HTMX + markdown 렌더링) — 옵션.
6. 변경 후 자동으로 KB 캐시 무효화 (`knowledge.reset_cache()`).

## Acceptance criteria

- 새 문서 생성하면 즉시 다음 인바운드 답장에서 참조됨.
- 삭제하면 즉시 빠짐.

## Verify

브라우저에서 직접 + `tests/test_knowledge_routes.py`.
