# 054 — Google Custom Search 소스 (대학·학회·종교/기타)

## Why

사용자 명세: 구글에서 대학·학회·종교 검색해서 발굴. Google Custom Search API 무료 100/일 + 결과 URL 들의 footer/contact 페이지에서 이메일 추출.

## What to do

1. `src/integrations/google_search.py` 신규 — Google Custom Search API 클라이언트.
   - `.env`: `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_ID`.
   - `search(query, num=10) -> list[{title, snippet, link}]`.
2. `src/agents/outbound/sources/google_search.py` 신규:
   ```python
   class GoogleSearchSource:
       name = "google_search"
       def discover(self, filters):
           # filters: {"query": "...", "category": "university|conference|religious|other", "max_results": 10, ...}
   ```
   - 검색 결과 URL 들을 순회.
   - [[057]] 의 페이지 footer 이메일 추출 모듈 호출.
   - 결과를 `ProspectCandidate` 로 매핑. `extra = {"category": ..., "search_snippet": ...}`.
3. `source_registry.py` 에 등록.
4. `src/llm/prompts/outbound/email_google_search.md` 신규 — 대학/학회/종교 톤별 분기 (LLM 이 `category` 보고 자동 분기).

## Acceptance criteria

- API 키 셋업 후 "Korean university AI lab" 같은 쿼리로 후보 5+ 개 반환.
- 각 후보의 `email` 필드는 페이지 footer 에서 발견된 경우만 채워짐.
- `category=religious` 인 경우 별도 라벨 (사용자 검토용).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_google_search_source.py -q
```

## Risks

- Google CSE API 일 100건 한도. 그 이상은 $5/1000.
- 종교/사이비 검색은 brand 리스크 — 자동 발송 X, 사용자 검토 필수 (UI 에 빨간 라벨).
