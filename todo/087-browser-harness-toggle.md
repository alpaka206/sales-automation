# 087 — browser-harness 백엔드 토글 추가 (Playwright 와 양립)

> ⚠️ **BLOCKED**: 아키텍처 의사결정 대기 중.
> `docs/2026-05-17_아키텍처_의사결정_요청.md` 참고.
> - A (클라우드) 채택 시 → 이 todo **폐기** (서버에선 browser-harness 무의미)
> - B (노트북) / C (mini PC) 채택 시 → 이 todo 진행
>
> 의사결정 전까지는 이 todo를 처리하지 말 것.

## Why

사용자 원 명세에서 "browser harness 라이브러리로 크롤링 하도록" 명시했는데
현재 Playwright + Gemini(Vertex) 직접 결합으로 구현됨. 두 백엔드
모두 지원하도록 환경변수로 토글 추가.

browser-use (`github.com/browser-use/browser-use`):
- Playwright 기반 AI 드리븐 브라우저 에이전트 (자연어 액션)
- Gemini 네이티브 지원: `ChatGoogle(model="gemini-2.5-flash", vertexai=True)`
  → Vertex 서비스 계정(GOOGLE_CREDENTIALS_JSON)로 인증, 별도 API 키 불필요
- Self-healing (실행 중 액션 재시도/적응)
- 헤드리스 가능 (서버 운영에 적합)

## What to do

1. `.env.example` 에 `BROWSER_BACKEND=playwright` 추가 (값: `playwright` |
   `browser_use`).
2. `src/integrations/ai_browser.py` 의 `fetch_and_extract_sync` 를 백엔드
   디스패처로 변경. `BROWSER_BACKEND=browser_use` 면 새 함수
   `_fetch_via_browser_use(url, instruction)` 호출.
3. `src/integrations/browser_use_adapter.py` 신규:
   - `from browser_use import Agent, ChatGoogle`
   - `ChatGoogle(model=settings.GEMINI_MODEL, vertexai=True)` 로 LLM 구성
     (Vertex 인증은 GOOGLE_CREDENTIALS_JSON 재사용)
   - 반환: 추출된 텍스트 또는 schema 객체
4. `docs/배포.md` 에 "browser-use 설치" 섹션 추가:
   - `pip install browser-use && playwright install chromium`
   - Vertex 자격 증명(GOOGLE_CREDENTIALS_JSON)이 설정돼 있으면 그대로 사용
5. linkedin_comments, linkedin_profile, google_search, job_board 의
   browser 호출이 토글을 그대로 따르는지 확인 (이미 ai_browser 통해서
   호출하므로 자동 적용되어야 함).
6. 단위 테스트: 토글 값에 따라 분기되는지만 검증 (mock subprocess).

## Acceptance criteria

- `BROWSER_BACKEND=playwright` (기본) 일 때 기존 동작 유지.
- `BROWSER_BACKEND=browser_use` 일 때 browser-use + ChatGoogle(vertexai=True)
  로 추출.
- 토글 변경에 코드 수정 불필요 (.env 만 바꾸면 동작 전환).
- `docs/배포.md` 의 browser-use 설치 가이드가 비개발자도 따라할 수
  있게 명시적.

## Verify

```powershell
# 1. Playwright 백엔드 (기본)
.venv\Scripts\python.exe -m pytest tests/test_ai_browser.py -q

# 2. browser-use 백엔드 (토글)
$env:BROWSER_BACKEND="browser_use"
.venv\Scripts\python.exe -m pytest tests/test_browser_use_adapter.py -q
```

## Risks

- browser-harness 는 사용자 Chrome 의존 — 운영 PC 가 항상 Chrome 켜져
  있어야 함. 무인 24/7 운영은 어려움.
- LinkedIn anti-bot 회피에는 훨씬 안전. 대신 PC 가 sleep 들어가면 멈춤
  (무인 운영 시 OS 전원 설정에서 절전을 꺼야 함).
