# knowledge_base/ (백업 / 초기 import 원본)

> **DB가 source of truth입니다.** 이 폴더의 .md 파일들은 `scripts/import_knowledge_base.py`를 통해 DB(`knowledge_documents` 테이블)로 일괄 import됩니다. import 이후에는 웹 UI 또는 DB에서 직접 편집하세요. 이 폴더는 원본 백업용으로 보존합니다.

## 최초 import 방법

```powershell
.venv\Scripts\python.exe scripts/init_db.py
.venv\Scripts\python.exe scripts/import_knowledge_base.py
```

같은 스크립트를 두 번 실행해도 중복 없이 upsert 처리됩니다.

## 파일 형식 (import 스크립트가 파싱하는 형식)

```markdown
---
title: 2026 요금제
categories: [pricing_question, purchase_inquiry]
---

본문 내용
```

### frontmatter 필드

- `title` *(선택)* — 제목. DB `title` 컬럼으로 매핑.
- `categories` *(권장)* — 인바운드 분류 매칭용. DB `categories` JSON 컬럼으로 매핑.
  - 가능한 값: `purchase_inquiry`, `partnership`, `pricing_question`, `support`, `recruiting`, `other`
  - 모든 카테고리에 매칭하려면 `[all]`.
  - 생략 시 `[all]`로 간주.

`slug`는 파일명에서 자동 도출됩니다 (예: `pricing_example.md` -> `pricing_example`).
`scope`는 import 시 기본값 `both`로 설정됩니다.
