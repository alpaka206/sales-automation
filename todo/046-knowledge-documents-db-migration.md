# 046 — knowledge_documents 테이블 + 기존 .md 일괄 import

## Why

`knowledge_base/` 디렉토리의 .md 파일들을 웹 UI 에서 편집 가능하게 하려면 DB 로 옮겨야 함. 텍스트 데이터라 DB 저장이 적합. 기존 파일은 자동 import.

## What to do

1. `src/db/migrations/0004_knowledge_documents.py` 신규 — 테이블:
   - `id` (PK)
   - `title` (str, not null)
   - `slug` (str, unique) — URL 친화
   - `categories` (JSON list of strings)
   - `scope` (enum: `inbound` / `outbound` / `both`, 기본 `both`)
   - `body` (TEXT)
   - `created_at` / `updated_at`
2. `src/db/models.py` 에 `KnowledgeDocument` 추가.
3. `scripts/import_knowledge_base.py` — 기존 `knowledge_base/*.md` 읽어서 DB 에 upsert. frontmatter 의 `categories` 그대로 매핑. `scope` 는 default `both`. slug 는 파일명에서 도출.
4. README.md 와 비슷한 안내 파일은 import 에서 제외.

## Acceptance criteria

- 마이그레이션 적용 후 `knowledge_documents` 테이블 존재.
- `python scripts/import_knowledge_base.py` 실행 후 기존 .md 파일들이 DB rows 로 변환됨.
- 같은 스크립트 두 번 실행해도 중복 없음 (upsert).

## Verify

```powershell
.venv\Scripts\python.exe scripts\init_db.py
.venv\Scripts\python.exe scripts\import_knowledge_base.py
.venv\Scripts\python.exe -m pytest tests/test_knowledge_db_model.py -q
```
