# Sales Automation

내부용 세일즈 자동화 시스템. 인바운드 문의 처리, 아웃바운드 리드 발굴, 일간/주간 리포트를 자동화합니다. 모든 발송은 사람이 승인한 후에만 나갑니다.

## 빠른 시작 (비개발자용)

### 이 프로그램이 하는 일

1. **인바운드 에이전트** — HubSpot으로 들어온 문의를 AI가 분석하고 답장 초안을 작성합니다.
2. **아웃바운드 에이전트** — YouTube, LinkedIn, CSV 파일에서 잠재 고객을 찾아 첫 이메일 초안을 작성합니다.
3. **리포트 에이전트** — 위 두 에이전트의 활동을 일간/주간 리포트로 정리합니다.

AI가 작성한 이메일은 **Slack 또는 Teams**에서 승인/수정/거절할 수 있으며, 승인 전에는 절대 발송되지 않습니다.

### 사전 준비물

- **Python 3.11 이상** — [python.org/downloads](https://www.python.org/downloads/) (설치 시 "Add Python to PATH" 체크)
- **인터넷 연결**
- **자격 증명** (.env 파일에 입력):
  - `INTERNAL_API_TOKEN` — 내부 API 보안 토큰 (필수, 랜덤 문자열)
  - `HUBSPOT_PRIVATE_APP_TOKEN` — HubSpot 인바운드/아웃바운드용
  - `SLACK_BOT_TOKEN` + `SLACK_APPROVAL_CHANNEL_ID` — 승인 알림용
  - 또는 `TEAMS_WEBHOOK_URL` — Teams 사용 시
  - `GOOGLE_CREDENTIALS_JSON` — LLM 호출용 Vertex AI 서비스 계정 JSON (필수)
  - 기타 선택: Gmail App Password, YouTube API Key

각 자격 증명 발급 방법은 [docs/설정.md](docs/설정.md)를 참고하세요.

### 3단계 설치

```
1. scripts\setup.bat 더블클릭
   → Python 확인, 가상 환경 생성, 의존성 설치, DB 초기화 자동 수행

2. .env 파일 열어서 자격 증명 입력
   → 메모장으로 .env를 열고 빈 값을 채우세요

3. scripts\run.bat 더블클릭
   → http://127.0.0.1:8000 에서 서버 시작
```

### 일상 사용

- **서버 실행**: `scripts\run.bat` 더블클릭
- **메시지 승인**: Slack/Teams에 카드가 도착하면 Approve/Reject 클릭
- **리포트 확인**: 매일 18시, 매주 토요일 09시에 Slack/이메일로 발송
- **아웃바운드 리드 추가**: 웹 UI 의 `/outbound/new` 에서 자연어 입력 또는 CSV 업로드
- **문제 해결**: [docs/문제해결.md](docs/문제해결.md) 참고

---

## 개발자용 가이드

### 아키텍처

```
HubSpot (CRM)  →  FastAPI BE (인바운드 폴러 + 발송 워커 내장)  →  LLM (Gemini / Vertex AI)
                                            ↓
                       SQLite/Postgres DB + 웹 UI 승인 + 발송 (HubSpot/SMTP/WhatsApp)
```

### 개발 환경 설정

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python scripts/init_db.py
python -m src.cli doctor
```

### 테스트

```bash
pytest -q                    # 전체 테스트
pytest tests/test_xxx.py -v  # 개별 파일
ruff check src tests         # 린트
```

### 폴더 가이드

| 폴더 | 용도 |
|---|---|
| `plan/` | 설계 사양 |
| `todo/` | 다음에 할 작업들 (번호순) |
| `done/` | 완료된 작업 (히스토리) |
| `company_rules/` | 회사 규칙 (톤, 시그니처, 금지어) |
| `src/` | 소스코드 (FastAPI BE + 웹 UI + 백그라운드 워커) |
| `scripts/` | 배치 스크립트, 초기화 |
| `knowledge_base/` | 회사 안내 문서 (.md) — 인바운드 답장 자동 참고 |
| `tests/` | pytest 테스트 |
| `data/` | SQLite DB (gitignored) |
| `logs/` | 앱 로그 (gitignored) |
| `docs/` | 사용법, 설정, 문제해결 가이드 |

### LLM 호출 방식

LLM은 **Gemini (Vertex AI)** 단독입니다. API 키가 아니라 **서비스 계정 JSON**으로 인증합니다 — `.env`의 `GOOGLE_CREDENTIALS_JSON`에 서비스 계정 JSON 전체를 넣고, `GOOGLE_CLOUD_PROJECT`(비우면 JSON의 project_id 사용)와 `GOOGLE_CLOUD_LOCATION`(예: `global`), `GEMINI_MODEL`(기본 `gemini-2.5-flash`)을 설정하면 됩니다.

### 배포

`plan/07_free_hosting_guide.md` 참고. 로컬 SQLite + uvicorn으로 충분하며, 필요 시 Render/Railway/Fly.io 무료 티어 사용 가능.
