# 047 — knowledge.py 로더 DB 기반으로 전환

## Why

[[046]] 으로 KB 가 DB 에 들어갔으니 `src/llm/knowledge.py` 의 로더가 파일 대신 DB 를 읽도록 전환. 인바운드/아웃바운드 양쪽에서 사용 가능.

## What to do

1. `src/llm/knowledge.py` 의 `load_relevant_docs(category, scope="inbound")` 시그니처:
   - scope 매개변수 추가 (`inbound` / `outbound` / `both`)
   - DB `KnowledgeDocument` 에서 `categories` 가 `category` 또는 `all` 을 포함하고 `scope` 가 인자와 호환되는 것만 조회.
   - 기존 frontmatter parser / file scan 코드 제거.
2. 기존 인바운드 `draft_reply` 호출은 `scope="inbound"`. (Phase 4 에서 아웃바운드 prompt 도 KB 사용하게 확장 예정.)
3. `tests/test_knowledge.py` 를 DB fixture 기반으로 재작성. `knowledge_base/*.md` 파일 의존성 제거.
4. `knowledge_base/` 디렉토리는 일단 보존 (사용자 백업용). README 만 갱신해서 "DB 가 source of truth, 이 폴더는 백업" 명시.

## Acceptance criteria

- 인바운드 `draft_reply` 호출 시 DB 에서 KB 가 정확히 조회됨.
- 기존 file-based 로더 코드 완전 제거.
- 모든 테스트 그린.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_knowledge.py tests/test_inbound_flow.py -q
```
