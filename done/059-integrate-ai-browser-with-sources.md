# 059 — AI 브라우저를 기존 소스에 통합

## Why

[[058]] 의 `ai_browser.fetch_and_extract` 를 기존 소스 코드에 연결. enrichment.py 와 새 소스들이 직접 Playwright 쓰는 부분 통일.

## What to do

1. `src/agents/outbound/enrichment.py` 의 `enrich_prospect` 가 `ai_browser.fetch_and_extract` 호출하도록 리팩토링:
   - 직접 httpx + regex 대신 AI 추출.
   - 추출 schema: `{"summary": str, "industry": str, "size_hint": str, "contact_emails": list[str]}`.
2. [[054]] `google_search.py` 의 후보 URL 처리도 `ai_browser` 경유로.
3. [[056]] `linkedin_profile.py` 도 가능한 한 `ai_browser` 추상화 통과.
4. 직접 Playwright 호출은 `ai_browser.py` 안에만 남도록 통일.

## Acceptance criteria

- `enrichment` 단위 테스트 통과 (mock ai_browser 응답).
- 다른 소스 코드에 `from playwright.*` import 없음 (`ai_browser` 만 가짐).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_enrichment.py tests/test_google_search_source.py tests/test_linkedin_profile_email.py -q
```
