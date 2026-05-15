# 068 — 도메인별 ICP 룰 편집 UI + DB

## Why

사용자 명세: 소스별로 ICP 점수 기준 다르게 설정 가능. 웹에서 룰 편집.

## What to do

1. `src/db/migrations/0009_icp_rules.py` 신규 — `icp_rules` 테이블:
   - id, source (str), criteria_md (TEXT), enabled BOOL, created_at/updated_at.
2. `OutboundAgent._score_icp()` 호출 시 해당 source 의 `criteria_md` 를 프롬프트에 inject.
3. 웹 UI:
   - `GET /icp-rules` — 소스별 룰 목록.
   - `GET /icp-rules/{source}/edit` — 룰 편집 (마크다운).
   - `POST /icp-rules/{source}` — 저장.
4. 룰 변경 후 다음 ICP 점수 계산부터 즉시 반영.

## Acceptance criteria

- youtube / linkedin_comments / google_search / job_board 각각 별도 룰 가질 수 있음.
- 같은 candidate 라도 룰 수정 후 다른 점수 가능.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_icp_rules.py -q
```
