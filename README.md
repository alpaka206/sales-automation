# PERSO Inbound

HubSpot 신규 문의를 받아 접수 확인 메일을 즉시 보내고, Gemini가 문의와 내부 정책 문서를 분석해 답변 초안을 준비하는 내부 운영 도구입니다. 상세 답변은 사람이 검토한 뒤 SMTP로 실제 발송하며, 성공한 이메일은 HubSpot 타임라인에 기록됩니다.

## 현재 동작 흐름

1. HubSpot `New` 티켓을 웹훅으로 받습니다.
2. 웹훅을 놓치면 10분 폴러가 변경된 `New` 티켓을 확인합니다. 다른 단계에서 생성된 뒤 `New`로 이동한 티켓도 포함합니다.
3. 첫 문의에는 “곧 자세한 답변을 드리겠다”는 접수 확인 이메일을 즉시 보냅니다.
4. Gemini가 문의를 분류하고 관련 내부 문서만 선택해 한국어 검토용 초안을 만듭니다.
5. 초안이 준비된 시점에만 Slack 알림을 보냅니다.
6. 운영자가 웹 UI에서 수정·번역·서명 선택 후 발송합니다.
7. SMTP 발송 성공 후 HubSpot 이메일 활동을 기록하고 티켓 단계를 이동합니다.

현재 개발자 HubSpot 계정에는 `Meeting link sent` 단계가 없어 임시로 `New(1) → Waiting on contact(2)`를 사용합니다. 실제 계정 연결 시 `.env`의 단계 ID만 바꾸면 됩니다.

## 운영 기능

- 답변 검토 큐와 이메일 미리보기
- HTML 서명·공개 HTTPS 이미지 URL 지원
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
- `src/api/web`: 운영 UI
- `company_rules`: 답변 원칙
- `knowledge_base`: AI가 선택해서 참고할 정책·제품 문서
- `src/db/migrations`: 기존 DB를 보존하는 순차 마이그레이션
