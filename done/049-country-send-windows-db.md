# 049 — country_send_windows 테이블 + 주요국 seed

## Why

국가별 최적 발송 시간 (현지 시각 기준). DB 에 임시값 저장. 스케줄러가 이 표 읽어서 다음 적절 시간 계산.

## What to do

1. `src/db/migrations/0005_country_send_windows.py` 신규:
   - `country_code` (PK, ISO 3166-1 alpha-2)
   - `country_name`
   - `timezone` (IANA, 예: `Asia/Seoul`)
   - `hours_start` (int 0–23)
   - `hours_end` (int 0–23)
   - `avoid_days_of_week` (JSON list of int 0=Mon ... 6=Sun)
2. seed 데이터 (주요 18개국):
   ```
   KR Asia/Seoul 9 11 [5,6]
   JP Asia/Tokyo 9 11 [5,6]
   US America/New_York 9 11 [5,6]   # ET 기준
   GB Europe/London 9 11 [5,6]
   DE Europe/Berlin 9 11 [5,6]
   FR Europe/Paris 9 11 [5,6]
   SG Asia/Singapore 9 11 [5,6]
   ID Asia/Jakarta 9 11 [5,6]
   VN Asia/Ho_Chi_Minh 9 11 [5,6]
   TH Asia/Bangkok 9 11 [5,6]
   IN Asia/Kolkata 10 12 [5,6]   # IN 은 토일도 일하지만 보수적
   PH Asia/Manila 9 11 [5,6]
   AU Australia/Sydney 9 11 [5,6]
   BR America/Sao_Paulo 9 11 [5,6]
   MX America/Mexico_City 9 11 [5,6]
   AE Asia/Dubai 10 12 [4,5]    # 금토 휴무
   IL Asia/Jerusalem 9 11 [4,5]
   default UTC 9 11 [5,6]
   ```
3. `src/agents/scheduler.py` 신규 — 함수 `compute_next_send_time(country_code, now_utc=None) -> datetime` 가 위 표 보고 다음 최적 시각 계산.

## Acceptance criteria

- 마이그레이션 + seed 후 18개 row 존재.
- `compute_next_send_time("KR", 일요일 자정 UTC)` → 월요일 09:00 KST → UTC 변환된 값.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_scheduler.py -q
```
