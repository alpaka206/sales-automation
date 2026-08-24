# PERSO Inbound

HubSpot 신규 문의를 받아 Gemini가 문의와 내부 정책 문서를 분석해 답변 초안을 준비하는 내부 운영 도구입니다. 고객에게 나가는 메일은 사람이 검토·승인한 뒤 SMTP로 발송하며, 성공한 이메일은 HubSpot 타임라인에 기록됩니다.

## 현재 동작 흐름

1. HubSpot `New` 티켓을 웹훅으로 받습니다.
2. 웹훅을 놓치면 10분 폴러가 변경된 `New` 티켓을 확인합니다. 다른 단계에서 생성된 뒤 `New`로 이동한 티켓도 포함합니다.
3. Gemini가 문의를 분류하고 관련 내부 문서만 선택해 한국어 검토용 초안을 만듭니다. 접수 직후 자동 메일은 보내지 않습니다.
4. 초안이 준비된 시점에만 Slack 알림을 보냅니다.
5. 운영자가 웹 UI에서 수정하고 **서명을 고른 뒤** 발송합니다. 외국어 문의는 `번역하기`를 완료해야 발송 버튼이 나타납니다. 서명은 발송할 때 붙습니다 — 초안 본문에는 서명이 없습니다.
6. SMTP 발송 성공 후 HubSpot 이메일 활동을 기록하고 티켓 단계를 이동합니다.

운영 HubSpot 단계 ID는 `.env.example`과 `render.yaml`의 7개 항목을 기준으로 설정합니다. 단계 이름이 바뀌어도 HubSpot 내부 ID가 같으면 기존 alias로 호환됩니다.

## 운영 기능

- 회신 및 검토 큐와 이메일 미리보기
- 서명 추가·수정·삭제 (글이든 HTML이든, 이미지는 공개 HTTPS URL)
- 동일 인물·동일 회사 도메인 히스토리
- Negotiation / 서비스 이용 고객 상태와 파이프라인
- HubSpot 이메일·Deal·메모 수동 동기화
- 미팅·카카오·전화·계약 등 수동 히스토리 입력
- 계약 금액·결제 예정일·입금일·만료일·언어쌍·Invoice·결제 링크 관리
- 답장 누락, 장기 미접촉, 갱신 임박, 플랜 업셀 후보 인사이트
- 견적 계산기와 Flex 품의용 계약값 복사
- 기존 형식을 보존하는 Google Sheets `Inbound DB`·`수주 DB` 동기화
- 서비스 계정 공유가 막힌 환경을 위한 관리자 Google 계정 OAuth 연결

## 시작

```powershell
scripts\setup.bat
# .env에 HubSpot, Vertex AI, SMTP, Slack 값을 입력
scripts\run.bat
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 설정 예시는 [.env.example](.env.example), 운영 설명은 [docs/사용법.md](docs/사용법.md)를 참고하세요.

## 개발

```powershell
.\.venv\Scripts\python.exe -m src.db.migrate
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

주요 폴더:

- `src/agents`: 문의 처리와 발송 워커
- `src/integrations`: HubSpot, SMTP, Slack
- `src/api`: FastAPI 라우트와 운영 UI 정적 자산
- `frontend`: React 운영 콘솔
- `src/llm/prompts`: 코드로 관리하는 프롬프트 골격
- DB `policy_sources` / `email_templates`: 콘솔에서 편집하는 내부 정책·제품 문서와 메일 템플릿
- `src/db/migrations`: 기존 DB를 보존하는 순차 마이그레이션
