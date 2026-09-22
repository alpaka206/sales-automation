# PERSO Inbound

HubSpot 신규 문의를 받아 Gemini가 문의와 내부 정책 문서를 분석해 답변 초안을 준비하는 내부 운영 도구입니다. 고객에게 나가는 메일은 사람이 검토·승인한 뒤 기존 HubSpot Conversations 스레드에 답장으로 발송됩니다.

## 현재 동작 흐름

1. HubSpot `New` 티켓을 웹훅으로 받습니다.
2. 웹훅을 놓치면 10분 폴러가 변경된 `New` 티켓을 확인합니다. 다른 단계에서 생성된 뒤 `New`로 이동한 티켓도 포함합니다.
3. Gemini가 문의를 분류하고 최근 대화·정책 원문을 같은 스냅샷으로 읽어 문의 언어의 초안을 만듭니다. 외국어 초안에는 한국어 대역을 함께 저장합니다. 접수 직후 자동 메일은 보내지 않습니다.
4. 초안이 준비된 시점에만 Slack 알림을 보냅니다.
5. 운영자가 웹 UI에서 수정하고 **서명을 고른 뒤** 승인합니다. 본문 언어가 문의 언어와 다르면 `번역하기`와 검토를 거칩니다. 발송 전에 승인 내용·정책·대화 변경을 다시 확인합니다. 서명은 발송할 때 붙습니다.
6. HubSpot Conversations 발송 성공 후 메시지 ID를 저장하고 티켓 단계를 이동합니다.

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
# .env에 HubSpot, Vertex AI, Slack 값을 입력
scripts\run.bat
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 설정 예시는 [.env.example](.env.example), 운영 설명은 [docs/사용법.md](docs/사용법.md)를 참고하세요.

## 개발

```powershell
.\.venv\Scripts\python.exe -m src.db.migrate
.\.venv\Scripts\python.exe -m pytest -q
# 임시 SQLite와 외부 네트워크 차단으로 로컬 회귀 검증
.\.venv\Scripts\python.exe scripts/check_policy_response.py --result-dir tmp/policy-check tests -q -ra
.\.venv\Scripts\ruff.exe check --no-cache src tests scripts/check_policy_response.py
cd frontend
npm.cmd test
npm.cmd run build
```

주요 폴더:

- `src/agents`: 문의 처리와 발송 워커
- `src/integrations`: HubSpot Conversations/CRM, Google Sheets, Slack
- `src/api`: FastAPI 라우트와 운영 UI 정적 자산
- `frontend`: React 운영 콘솔
- `src/llm/prompts`: 코드로 관리하는 프롬프트 골격
- DB `policy_sources` / `email_templates`: 콘솔에서 편집하는 내부 정책·제품 문서와 메일 템플릿
- `src/db/migrations`: 기존 DB를 보존하는 순차 마이그레이션

## 정책 응답 설계와 검증

현재 구조에 근거 스냅샷·승인 재확인을 추가했습니다. 리마인더는 기존 시퀀스를 유지하고, 이전 티켓은 요약 대신 실제 기록을 테두리 박스로 표시합니다. 이번 작업에는 새 DB migration이 없습니다.

- [실제 구조와 구현 한계](docs/architecture/policy-response-system.md)
- [선택과 대안을 기록한 ADR](docs/adr/2026-09-21-policy-response-boundaries.md)
- [코드·공식 API 조사 근거](docs/research/policy-response-evidence.md)
- [로컬 검증과 실제 Gemini 비교 결과](docs/evaluation/policy-response-evaluation.md)
- [장애·승인·개인정보·복구 절차](docs/operations/policy-response-runbook.md)
- [최신 실행 결과](docs/policy-response/runs/2026-09-21-live/results.md) / [기계 판독 JSON](docs/verification/policy-response-result.json)

위 테스트 명령은 실제 Gemini 평가가 아닙니다. 사용자의 후속 지시에 따라 합성 개발셋으로 실제 Vertex 비교를 수행했고, 답변 요소 조합·제한 근거 검사를 추가했습니다. 현재 회사 정책은 조회 timeout/401로 확보하지 못했습니다. 의미 오류와 초안 차단이 남아 있으며 운영 발송·배포 승인을 뜻하지 않습니다.

```powershell
# 설정 존재 여부만 표시, 외부 호출 없음
.\.venv\Scripts\python.exe scripts/evaluate_policy_response.py --preflight
# 실제 유료 Vertex 호출, 합성 fixture + 임시 SQLite, 고객 발송 없음
.\.venv\Scripts\python.exe scripts/evaluate_policy_response.py --live --dataset tests/fixtures/policy_response_live.json --arms router all --repeats 2 --max-calls 220 --output tmp/policy-eval-new --authorized-by "<이름>"
```

원시 평가 입력/응답은 tmp 아래에 두며 Git에서 제외합니다. 가상 정책은 회사 정책이나 운영 seed가 아닙니다. 최종 본문을 질문별 답변 요소로 조합한 이유와 실패 사례는 [후속 ADR](docs/adr/2026-09-21-grounded-draft-composition.md)에 있습니다.
