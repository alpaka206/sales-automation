# 055 — 채용 페이지 소스 (사람인·잡코리아)

## Why

사용자 명세: 사람인/잡코리아의 성형외과 마케팅, 병원 SNS 같은 공고 → 회사명 + 채용 담당자 + 회사 도메인 추출 → 영업 대상.

## What to do

1. `src/agents/outbound/sources/job_board.py` 신규:
   - `discover(filters)` 받는 keyword + region.
   - Google CSE 에 `site:saramin.co.kr "{keyword}"` 와 `site:jobkorea.co.kr "{keyword}"` 검색 — 직접 스크래핑 대신 검색으로 우회 (anti-bot 회피).
   - 결과 URL 의 공고 페이지 [[058]] 의 ai_browser 로 회사명/공고 직무/회사 홈페이지 URL 추출.
   - 회사 홈페이지 [[057]] 의 footer 추출로 contact 이메일.
2. `JOB_BOARD_SITES` env 로 사이트 리스트 커스터마이즈 가능 (`saramin.co.kr,jobkorea.co.kr,wanted.co.kr`).
3. `source_registry.py` 등록.

## Acceptance criteria

- "성형외과 마케팅" 키워드 → 사람인/잡코리아 공고 5+ 건 후보.
- 각 후보에 `company` + `domain` 채워짐 (이메일은 best-effort).
- 직접 사이트 HTML 스크래핑 안 함 (Google CSE 경유).

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_job_board_source.py -q
```

## Risks

- 사람인/잡코리아 anti-bot 강함. CSE 결과 페이지 fetch 도 차단 가능. fallback 으로 후보 URL 만 리턴하고 사용자가 수동 보강.
