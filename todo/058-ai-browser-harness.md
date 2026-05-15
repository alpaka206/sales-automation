# 058 — AI 브라우저 하네스 (Playwright + claude CLI 결합)

## Why

사용자 명세: Playwright 직접 호출보다 AI 드리븐 방식. browser-use 같은 OSS 라이브러리는 LLM API key 가 필요. claude CLI 무료 유지하면서 비슷한 효과 얻으려면 자체 작성 — Playwright 로 페이지 HTML 가져오고, claude CLI 에 추출 지시.

## What to do

1. `src/integrations/ai_browser.py` 신규:
   ```python
   async def fetch_and_extract(
       url: str,
       extraction_prompt: str,
       schema: type[BaseModel] | None = None,
       cookies: list[dict] | None = None,
       max_html_chars: int = 30000,
   ) -> Any:
       """Playwright 로 URL 페이지 로드 → DOM 안정화 대기 → outerHTML 일부 →
       claude CLI 에 추출 프롬프트 + HTML 던져서 구조화 응답."""
   ```
2. HTML 자르기 전 noise 제거 (`<script>`, `<style>`, 광고 영역 등). `_strip_html` 함수 재사용.
3. 다음 시나리오에서 활용:
   - [[054]] Google 검색 결과 페이지 → 사이트별 contact 정보 추출
   - [[056]] LinkedIn 프로필 → Contact info
   - [[057]] 일반 회사 페이지 → 이메일 + 회사 한 줄 요약
4. Concurrent fetch 제한: 최대 3 페이지 동시. 페이지 사이 random sleep.

## Acceptance criteria

- 임의 URL 받아서 "이 페이지에서 회사명, 대표 이메일, 한 줄 요약 뽑아줘" 같은 자연어 지시로 구조화 JSON 응답.
- LLM 호출 횟수 제한 (한 페이지당 1회).
- 단위 테스트는 file:// fixture HTML 로.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ai_browser.py -q
```

## Risks

- HTML 30k 자르면 정보 손실. 핵심 영역 (heading, footer, contact) 위주로 남기는 전처리 강화.
- Playwright Chromium 200MB+ 다운로드 — `setup.bat` 안내 갱신.
