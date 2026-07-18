# knowledge_base/ (백업 / 초기 import 원본)

> **DB가 source of truth입니다.** 이 폴더의 .md 파일들은 `scripts/import_knowledge_base.py`를 통해 DB(`knowledge_documents` 테이블)로 일괄 import됩니다. import 이후에는 웹 UI(`/knowledge`) 또는 DB에서 직접 편집하세요. 웹 UI에서 편집하면 매 저장마다 이전 내용이 `knowledge_document_revisions`에 자동 스냅샷되어 수정 이력이 남습니다. 이 폴더는 원본 백업용으로 보존합니다.

## 최초 import 방법

```powershell
.venv\Scripts\python.exe scripts/init_db.py
.venv\Scripts\python.exe scripts/import_knowledge_base.py
```

같은 스크립트를 두 번 실행해도 중복 없이 upsert 처리됩니다. `_`로 시작하는 파일(`_TEMPLATE.md` 등)과 `README.md`는 건너뜁니다.

## 새 문서 작성

`_TEMPLATE.md`를 복사해서 시작하세요:

```powershell
Copy-Item knowledge_base\_TEMPLATE.md knowledge_base\perso_faq.md
```

## 파일 형식 (import 스크립트가 파싱하는 frontmatter)

```markdown
---
title: 2026 요금제
categories: [pricing_question, purchase_inquiry]
tags: [요금제, 가격, 플랜]
summary: 셀프서브·엔터프라이즈 요금 구조와 가격 응대 어조.
scope: inbound
author: PERSO Sales
status: active
created_at: 2026-06-02
updated_at: 2026-06-02
---

본문 내용
```

### frontmatter 필드

| 필드 | 필수 | 설명 |
|------|------|------|
| `title` | 권장 | 제목. DB `title` 컬럼. 생략 시 파일명. |
| `categories` | 권장 | 인바운드 분류 매칭(폴백 경로). DB `categories` JSON. 가능 값: `purchase_inquiry`, `partnership`, `pricing_question`, `support`, `recruiting`, `other`. 모든 카테고리는 `[all]`, 생략 시 `[all]` 취급. |
| `tags` | 권장 | LLM 문서 라우터가 참고하는 키워드 리스트. |
| `summary` | **강력 권장** | 한 줄 요약. **LLM 라우터가 본문 대신 이 줄을 읽고 관련성을 판단**하므로 핵심을 정확히. |
| `scope` | 선택 | 인바운드 답변용 `inbound`를 사용합니다. 기존 공통 문서는 `both`도 읽을 수 있습니다. |
| `author` | 선택 | 작성자. |
| `status` | 선택 | `active`(기본) / `draft` / `archived`. **`active`만 LLM에 노출**됩니다. |
| `created_at`, `updated_at` | 선택 | `YYYY-MM-DD`. DB에도 타임스탬프가 자동 관리됩니다. |

`slug`는 파일명에서 자동 도출됩니다 (예: `perso_pricing.md` → `perso_pricing`).

## AI가 문서를 찾는 방식 (LLM 라우터)

인바운드 답장 작성 시, `src/llm/knowledge.py:select_relevant_docs`가:

1. `status: active` + `scope` 호환 문서들의 **인덱스**(slug + title + summary + tags + categories)를 만들고,
2. Gemini(flash)에게 **실제 문의 내용**과 인덱스를 주어 "이 문의에 관련된 문서 slug"를 고르게 한 뒤,
3. 선택된 문서 본문만 초안 작성 프롬프트(Gemini pro)에 주입합니다.

라우터 호출이 실패하거나 아무것도 고르지 못하면 기존 `categories` 정확 매칭으로 자동 폴백합니다. 그래서 `summary`/`tags`가 정확할수록 더 알맞은 문서가 선택됩니다.
