# 056 — LinkedIn 프로필 페이지 이메일 추출

## Why

`linkedin_comments` 소스가 발견한 사람들의 이메일을 LinkedIn 프로필 "Contact info" 에서 가져옴 (사용자 명세). 노출되어 있을 때만.

## What to do

1. `src/integrations/linkedin_profile.py` 신규:
   - `fetch_profile_email(profile_url: str, session_cookie: str) -> str | None`.
   - Playwright 로 프로필 페이지 로드 (li_at 쿠키 사용).
   - "Contact info" 버튼 클릭 → 모달 열림 → email 영역 추출.
   - 캡차 / 차단 페이지 감지 → None 리턴 + WARN 로그.
2. `linkedin_comments` 소스의 `discover()` 끝에 후보별 이메일 시도:
   - `MAX_EMAIL_LOOKUPS_PER_RUN=20` (rate limit).
   - 발견된 이메일은 `ProspectCandidate.email` 채움.
   - 못 찾으면 그대로 둠 (운영자가 수동 입력 가능).
3. 통계 로그: "12/20 lookups returned email" 같은 식.

## Acceptance criteria

- `LINKEDIN_SCRAPING_ENABLED=true` 일 때만 동작.
- 단위 테스트: 모의 HTML fixture 로 Contact info 모달에서 이메일 정확히 추출.
- 차단 페이지 (challenge) 만나면 그 후보 스킵 + 다음 진행.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_linkedin_profile_email.py -q
```

## Risks

- LinkedIn 의 anti-bot 매우 강함. 20건 이상 한 세션에서 시도하면 계정 차단.
- 이메일 노출은 일반적으로 1차 연결 한정 — 발견율 30% 미만.
