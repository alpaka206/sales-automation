# 052 — Prospect 상태 파이프라인 enum + 전이 로직

## Why

사용자 명세: `가져옴 / 메일발송 / 메일응답 / 진행중 / won / lost`. 현재 `Prospect.status` 는 자유 문자열. enum 으로 제한 + 전이 로직 명시.

## What to do

1. `src/db/migrations/0007_prospect_status_enum.py`:
   - `prospects.status` 값 표준화: `collected` / `analyzed` / `sent` / `replied` / `in_progress` / `won` / `lost` / `skipped_lowscore` / `skipped_dup` / `bounced`
   - 기존 row 의 값 매핑 (`candidate` → `collected`, `drafted` → `analyzed`, 등).
2. `src/agents/outbound/status.py` 신규 — 전이 함수:
   - `transition(prospect_id, new_status, reason: str|None)` — 유효한 전이만 허용 (matrix 코드 안에 명시).
   - 무효 전이는 `InvalidStatusTransition` 예외.
3. 자동 전이 지점:
   - 소스 discover 후 → `collected`
   - ICP/draft 완료 → `analyzed`
   - 발송 워커 성공 → `sent`
   - reply_check 감지 → `replied`
   - 사용자 수동 → `in_progress`, `won`, `lost` (웹 UI 에서)
4. UI 한국어 표시는 변환 테이블 `KR_LABELS = {"collected": "가져옴", ...}` 으로.

## Acceptance criteria

- enum 위반 시 DB 또는 코드 레벨에서 예외.
- 전이 matrix 가 코드에 명시되어 있음.
- 기존 데이터 마이그레이션 후 검증.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_prospect_status.py -q
```
