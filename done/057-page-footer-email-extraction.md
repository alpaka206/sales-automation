# 057 — 일반 페이지 footer/contact 이메일 추출 모듈

## Why

Google 검색·채용 공고 발굴 시 회사 홈페이지에서 이메일을 footer/contact 페이지에서 자동 추출. 공통 모듈로 재사용.

## What to do

1. `src/integrations/email_discovery.py` 신규:
   ```python
   def extract_emails_from_html(html: str) -> list[str]:
       """정규식 + obfuscation 패턴 (예: 'name at domain dot com') 정규화"""
   
   def discover_emails_from_url(url: str, timeout=5) -> list[str]:
       """url 페이지 + footer/contact 링크 한 단계 follow."""
   ```
2. obfuscation 패턴 대응:
   - `name [at] domain [dot] com`
   - `name(at)domain(dot)com`
   - 자바스크립트로 가려진 mailto: (Playwright 옵션으로 fetch 시 처리)
3. 회사 이메일 우선 (도메인 일치하는 것)/개인 메일 후순위.
4. `enrichment.py` 와 호출 결합 — 도메인이 있는 후보에 대해 자동 시도.

## Acceptance criteria

- HTML fixture (`tests/fixtures/contact_*.html`) 에서 정확히 이메일 추출.
- 회사 도메인 우선순위로 정렬.
- 동일 페이지 재방문 안 함 (캐싱 in-memory).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_email_discovery.py -q
```
