# 초기 분석 — 상세 참고문서 열람 전 고정

기준 commit: `95ec3d5c36d5eab58d7e03cfc469ea72f9332036`.
기존 변경: 추적 파일 변경 없음. 사용자 미추적 문서 4개(`docs/데이터-스냅샷-로컬-분석-설계.md`, `docs/데이터-에이전트-설계.md`, `docs/로컬-웹-연결-조사요청.md`, `docs/수주고객-사용현황-데이터검증-2026-09-15.md`)와 입력 패키지 두 디렉터리가 있음. 보존한다.

읽은 계약: `policy-response-final/00-CLI-PROMPT.md`, 실제 발견 위치 `policy_response_final_cli_package/ai-policy-redesign/MASTER_TASK.md`, 보고 형식 `REPORT_CONTRACT.md`, 관련 `CLAUDE.md` 규칙. `AGENTS.md`는 관련 경로에서 발견하지 못했다. 상세 specs/references/acceptance는 아직 열람하지 않았다. 단일 세션 분석이며 독립 blind 비교가 아니다.

## 실제 경로

Python/FastAPI → webhook 또는 폴러 → durable inbound job → `InboundAgent.run` → 연락처/최초 문의 → placeholder 저장 → 기존 대화/보강 정보 → 분류 → `select_relevant_docs`(flash) → `_draft_reply`(pro) → 언어·가격·링크 가드 → `_finalize_draft`의 pending_approval → 사람 승인 → send worker → Conversations 이메일 회신. 기존 외부 쓰기 gate와 delivery_unknown 격리는 유지한다.

정책은 콘솔이 `policy_sources` 원문과 version을 쓰고 이전 판본은 revisions에 보존한다. `model_access`와 첫/후속 scope를 쿼리에서 거른다. rules와 knowledge를 별도 조회한다. AI usage_note는 검색용 파생물이다. 운영 정책 DB와 로컬 app.db는 열지 않았다.

리마인더는 `followup_sequence`의 3/5/7일 템플릿 시퀀스다. 사용자의 “리마인더는 리마인더대로” 요청에 따라 설정과 발송 흐름을 보존한다. 답변 생성만 리마인더를 정책상 실질 답변으로 세지 않아야 한다.

## 기존 검증

`scripts/check_policy_response.py --result-dir docs/policy-response/runs/2026-09-21-local/baseline-corrected tests -q`: **1610 passed, 43 skipped, 1 warning**, pytest exit 0. 임시 SQLite, 가짜 인증, 외부 socket/DNS 차단. Starlette/httpx deprecation 경고. live Gemini는 NOT_RUN.

실행환경 문제: 최초 Python sandbox 실행은 uv trampoline permission denied. 승인된 로컬 테스트 명령으로 실행했다. 최초 harness가 Windows asyncio의 loopback socketpair까지 막아 다수 테스트가 실패하여 중단했다(`baseline/pytest.txt`, 진단 `tmp/policy-baseline-diagnostic`). 외부 통신 차단은 유지하고 숫자 loopback만 허용한 재실행이 위 기준 결과다. 이를 제품 결함으로 세지 않는다. 일부 기존 테스트가 sys.argv를 변경하므로 기준 command.json의 command는 `x`로 오염되었다; 실제 명령은 본 문단과 pytest_args에 남아 있다. harness에서 argv를 실행 전에 보존할 예정이다.

## 현재 코드에서 확인한 결함 후보와 최소안

1. `_build_conversation_context`: summary + requests + 오래된 순 대화를 합친 뒤 앞 6000자를 취한다. 긴 요약이 최신 원문/정정을 밀어낸다. 최근 원문부터 예산을 배정하고 잘림·파생요약·고객 주장 여부를 표시한다. facts 엔진은 추가하지 않는다.
2. `latest_customer_message`: 모든 outgoing 뒤로 기준선을 이동한다. `last_sent_reply`와 달리 reminder를 제외하지 않아서 리마인더 직전 미답변 문의가 사라진다. 실제 회신만 기준선으로 사용한다. `test_sent`도 실제 발송으로 읽는 경로를 재현한다.
3. `_rules_from_db`: DB 오류를 빈 문자열로 바꿔 정책 없는 생성을 허용한다. 오류와 실제 빈 정책 집합을 구별해 해당 초안을 실패 상태로 남긴다. 고객 거절 메일은 만들지 않는다.
4. 규칙·검색 문서의 서로 다른 시점 조회와 원문/version 연결 부재: 새 서비스 대신 요청 단위 스냅샷/기존 Event 활용의 비용을 검토한다. 원문을 로그에 복제하지 않는다. 적용일·계약 우선순위는 현재 모델에 없으므로 날짜를 추론하는 엔진을 만들지 않는다.
5. UI 추가 요청: 티켓 상세 이전 히스토리와 수주 고객 히스토리가 ticket.summary 한 문단을 보여준다. 실제 소통 기록을 같은 UI 컴포넌트로 표시하고 티켓마다 테두리 박스로 구분한다. 리드 상세의 중복 티켓 요약도 제거한다.

## 아직 결정할 수 없는 것

- 실제 정책의 시행일/계약별 예외/우선순위는 담당자가 정해야 한다. seed/첨부 가상 정책을 실제 정책으로 쓰지 않는다.
- RAG 대 장문 전체 문맥, rules DSL, 검증 LLM 추가가 유효 답변율을 높이는지는 미측정. 기존 경로 최소 보강을 우선한다.
- 정상 JSON/실제 인용은 정책 판단의 정답 증명이 아니다. 의미적 정책 검증과 자연스러움은 mock으로 입증하지 않는다.
- 외부 모델 데이터·인증·예산 승인이 없어 live Gemini/고객 전송/운영 migration은 NOT_RUN. 로컬 수정·검증은 계속한다.
