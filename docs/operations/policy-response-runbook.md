# 정책 응답 운영·복구 경계

상태: 로컬 구현 검증. **운영 배포·발송·CRM 변경·운영 migration은 NOT_RUN**. 이 문서는 운영 행동 승인서가 아니다.

## 실행 경계

`scripts/check_policy_response.py`는 DATABASE_URL을 일회용 SQLite로 덮어쓰고 가짜 인증, 백그라운드 worker 중지, socket/DNS 외부 차단을 적용한다. Windows asyncio용 숫자 loopback은 허용한다. 기존 sender 테스트를 통과시키기 위해 프로세스 내부 쓰기 gate를 열지만 transport는 mock이고 외부 네트워크는 차단한다. 운영 설정 파일을 수정하지 않는다. 외부 child process까지 격리하는 범용 sandbox라는 주장은 하지 않는다.

일반 회신은 pending_approval → 사람 승인 → 기존 worker만 가능하다. 모델 출력은 private provenance/승인 권한을 채울 수 없다. 리마인더는 기존 템플릿·설정·고객 답장 확인 경로를 별도로 유지한다. 사용자가 요청한 기존 기능 유지이며 이번 작업에서 리마인더를 실제 발송하지 않았다.

## 실패 시 처리

| 신호 | 로컬 동작 | 운영자가 확인할 것 |
|---|---|---|
| 정책/대화 읽기 오류 | 초안 실패, 빈 정상 맥락으로 대체 안 함 | DB 복구 후 재생성 |
| 원문 인용/기간/실행 상태 검사 재실패 | 의미 재작성 1회 후 draft_failed; durable job은 dead로 끝내 자동 반복 생성 중단 | 근거와 실제 상태를 검토하고 수동 수정/재생성; 고객 거절로 바꾸지 않음 |
| 생성 중 정책/대화 변경 | 이전 초안 저장 거절 | 최신 정책·정정으로 재생성 |
| 승인 전 근거 변경 | pending 상태 유지, 승인 오류 | 초안 재생성·검토 |
| 승인 후 본문/수신/상태/근거 변경 | DeliveryPermanentError, 기존 worker의 send_failed 처리 | 새 내용 재검토·재승인; 근거 변경은 재생성 |
| 주소 조회 도중 로컬 정정 수신 | 실제 POST 직전 검사에서 중지 | 최신 대화로 재생성 |
| 전송 timeout/결과 불명확 | 기존 delivery_unknown 격리 | provider의 실제 발송 여부 확인 전 재전송 금지 |
| 발송 성공 뒤 CRM 동기화 실패 | 기존 post-send 복구만 재시도 | 고객 메일을 다시 보내지 않음 |

정책 조회가 정상이나 원문이 비어 있는 경우, 질문별 필수 정책 누락 여부는 현재 판정하지 못한다. 보수적인 초안/검토 경로를 유지하며, 빈 정책을 “허용”이나 “거절”로 치환하지 않는다.

## 데이터 목록과 수명

| 저장/전송 | 데이터 | 이번 변경 / 잔여 결정 |
|---|---|---|
| 기존 DB messages/interactions/policy_sources/revisions | 고객 원문·정책·이력 | 원천 보존 방식 유지, 기간·삭제 정책 담당자 결정 필요 |
| 새 reply_context Event | 내부 ID, 버전, 해시, 모델/API, 선택 ID, 검증 상태 | raw 원문 중복 저장 없음. 연결 가능한 메타데이터이므로 익명 자료로 취급하지 않음 |
| 새 reply_approval Event | message ID, 승인 내용 해시 | Approval transaction에 함께 저장. Event 보존/원천 삭제 전파 기간 미확정 |
| 일반 로그 | 오류 유형/개수/ID | 새 schema 오류·router reasoning/keys·가격 제거 문장 로그에서 raw 내용 제거. 기존 저장소 전체 로그의 PII 감사를 완료한 것은 아님 |
| 이번 시험 자료 | 합성 fixture·시험 결과 | 고객 데이터/운영 정책 없음. 원본 input packet의 예제는 운영 정책이 아님 |
| Vertex | 합성 정책·가상 문의로 실제 생성 평가 | 사용자 후속 지시에 따라 실행. 운영 고객 데이터/현재 회사 정책은 전송하지 않았음. API별 보존 조건 인증은 안 함 |
| tmp/policy-eval-2026-09-21 | 평가 원시 입력/응답/호출 기록 | Git 제외. 실제 데이터 평가 시 접근·보관·삭제는 별도 운영 정책 적용. 공개 보고에는 이번 합성 답변과 집계만 포함 |

human_only는 모델 선택 전 SQL 필터로 제외한다. 티켓 records는 contact와 conversation 소유권을 모두 확인한다. 이 저장소를 다중 tenant 서비스로 확장한 것은 아니며 portal 단위 격리·전체 권한 감사는 별도 과제다. 생성 모델에 쓰기 도구를 새로 부여하지 않았다. 프롬프트 구분자만으로 injection 방어 완료라고 하지 않는다.

## 배포 gate, migration, rollback

스키마/migration 추가 없음. 기존 Event payload를 확장했으며 정책 release와 완전한 dispatch snapshot을 새 테이블로 만든 것이 아니다. 실제 migration 실행 없음. 새 trace가 없는 legacy/manual/template 경로는 기존 동작을 유지한다. 새로 사람 승인한 manual 초안에는 승인 binding이 남지만 과거 정책·대화 snapshot을 소급 생성하지 않는다.

배포 책임자는 새 기능 적용 전 대기 중 legacy 초안을 재생성할 범위, Event 보존/삭제, 검토 담당자, 현재 모델 종료 대응을 결정해야 한다. 동적 서명 HTML·기본 발신 계정·링크 설정과 첨부 payload 전체는 아직 승인 hash에 고정하지 않는다(현재 sender에는 새 첨부 기능을 추가하지 않음). 외부 송신과 로컬 재확인 사이 경쟁 조건이 남는다.

코드/빌드 자산을 기준 버전으로 복구할 수 있으나 안전 검사가 함께 사라진다. Event 행을 삭제할 필요는 없다. 운영 rollback 전에 미발송 건 확인·외부 쓰기 gate 정책을 책임자가 승인해야 한다. 리마인더 설정을 자동으로 켜거나 끄는 migration/스크립트는 제공하지 않는다.

자동 정책 판단/발송 확대는 다음이 충족될 때만 별도 검토한다: 실제 정책 권위·시행일·계약 예외 명세, 실제 원문과 독립 문의의 Gemini 의미 평가, 실제 inbox 권한 contract 시험, legacy 초안/동적 출력 binding 대책. 합성 live 평가에서 의미 오류와 초안 차단이 남았으므로 자동 발송 확대 근거로 사용하지 않는다. 정책 DB read-only 연결은 timeout, 설정 인증을 사용한 GET API는 401이었다. 과거 백업에서 접근 권한을 추정해 외부 전송하지 않는다.

평가 실행: `scripts/evaluate_policy_response.py --preflight`는 설정 존재 여부만 표시한다. `--live --dataset tests/fixtures/policy_response_live.json --output tmp/... --repeats 2 --arms router all --max-calls 220`은 실제 유료 Vertex 호출이며 disposable SQLite에만 기록한다. `--max-calls`는 API 호출 수 상한이며 금액 상한이 아니다. 인바운드 handle의 CRM 보강/worker/전송 대신 classify→draft→finalize만 호출한다. 따라서 실제 운영 전체 E2E 검증으로 보고하지 않는다. 출력을 생산 DB로 옮기거나 가상 정책을 운영 seed에 넣지 않는다.
