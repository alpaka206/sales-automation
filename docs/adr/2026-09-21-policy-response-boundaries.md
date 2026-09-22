# ADR: 기존 라우터를 유지하고 요청 근거·승인 경계를 추가한다

날짜: 2026-09-21. 상태: **ACCEPTED for local implementation**, 운영 적용 NOT_RUN.

이 ADR은 초기 로컬 경계 개선 결정이다. 이후 실제 Gemini 실패에 따른 답변 요소 조합·제한 재작성·큐 재시도 제한은 [후속 ADR](2026-09-21-grounded-draft-composition.md)이 확장한다. 아래의 초기 호출 수/미측정 상태는 그 결정 시점 기준이다.

## Context

현재 저장소의 작은 policy_sources 원문과 LLM 라우터는 이미 동작한다. 반면 [5개 재현 실패](../policy-response/runs/2026-09-21-local/reproduced/pytest.txt)는 긴 요약의 정정 유실, 리마인더 기준선, test_sent 판정, 대화/정책 읽기 실패를 드러냈다. 별도 시점의 rules/knowledge 조회는 원문 버전 일관성을 증명하지 못했다. 사람 승인 상태만으로는 이후 본문/수신자/근거 변경을 검출하지 못했다.

## Decision

1. **MUST_FIX**: 최근 실제 대화 우선, first/followup의 실제 회신 기준 통일, 조회 실패 명시화, 요청 추출의 원문 통합.
2. **MUST_FIX**: 작은 frozen PolicySnapshot, 기존 Event에 서버 소유 provenance, 승인 binding, 발송 직전 재확인. 새 서비스/테이블/migration 없이 실제 경로에 연결한다.
3. **KEEP**: 원문 정책 편집, human_only 사전 필터, DB 작업 큐, 기존 flash 라우터/pro 생성, 사람 승인, send worker/불명확 전송 격리, 리마인더 시퀀스.
4. **MEASURE_FIRST**: 전체 허용 문서 대 현재 라우터, provider native structured output, 별도 질문별 결정 모델/validator, 모델 교체.
5. **DEFER**: 벡터 DB·그래프·범용 정책 DSL·추가 검증 LLM·새 브로커. 실제 실패를 해결하는 최소 대안보다 낫다는 측정이 없다.
6. UI는 동일 소통 기록 컴포넌트로 통합하고 요약 전용 중복 코드를 제거한다. 별도 프런트 데이터 저장소는 만들지 않는다.

## Alternatives and adversarial review

| 대안 | 반론 | 결론 |
|---|---|---|
| 프롬프트만 길게 수정 | DB 오류가 빈 값으로 바뀌거나 최신 원문이 잘린 것은 프롬프트로 복구 불가 | 배제 |
| 현재 코드 그대로 + 사람 검토 | 검토자가 어떤 버전으로 작성됐는지 모르고 승인 후 변경도 놓침 | 결함 수정 |
| 완전한 정책 컴파일러/규칙 엔진 | 실제 정책의 권위·시행일·계약 예외가 입력되지 않았음. 추출 오류가 실행 규칙으로 굳을 수 있음 | DEFER |
| 모든 문서 상시 주입 | 단순하고 예외 문서 누락을 줄일 수 있으나 길이/충돌/비용 증가 미측정 | paired 실험 |
| RAG/그래프 + 검증 LLM | 검색 누락과 공통 모델 오류가 추가됨. 작은 문서량에 인덱스/삭제/권한 동기화 비용 | 근거 생길 때 재검토 |
| 정책 행 version만 비교 | version 증가를 빠뜨린 직접 수정과 scope/access 변경을 못 잡음 | 내용·메타데이터 hash 채택 |
| 새로운 승인 테이블 | 기존 Event와 승인 transaction으로 현재 변경을 묶을 수 있음 | 기존 저장 구조 재사용 |
| 모든 legacy 초안 일괄 차단/삭제 | 수동 초안·기존 리마인더 동작 파괴, 운영 데이터 무단 변경 | 새 trace부터 적용, 배포 시 재생성 검토 |

## Evidence and tests

- E01~E06은 [근거 장부](../research/policy-response-evidence.md), 실행값은 [결과](../policy-response/runs/2026-09-21-local/results.md).
- 코드: policy_context.py, inbound.py, reply_safety.py, approval.py, integrations/senders/__init__.py.
- 테스트: test_policy_context.py의 실제 draft→pending→approve→transport mock 경로, 정책 변경 4종, 승인 내용/수신/상태 변경 6종, 원격 조회 중 정정; test_followup_draft.py, test_policy_model_access.py.
- UI: test_history_view.py, test_interaction_log.py, test_ticket_summary.py, frontend/test/ticket-history.test.tsx.
- 두 에이전트의 합의나 공개 가상 예제의 수를 정확도 근거로 삼지 않았다. 단일 세션 코드 검토와 로컬 fixture 결과다.

## Trade-offs and risks

새 추상화는 snapshot, approval binding, 화면 공통 record projection 세 가지다. 모델 호출을 추가하지 않지만 정책/대화/승인 Event 조회 횟수와 hashing 비용이 늘었다. Event JSON 조건 검색은 데이터 증가 시 느려질 수 있으며 p95 실측은 NOT_RUN. 보수적인 hash invalidation은 재작성/검토 부담을 높일 수 있다.

가격·링크 정규화는 기존 발송 동작을 보존한다. 최종 검사에서는 DB의 승인 원문을 비교하고 정규화된 메모리 본문의 일치는 강제하지 않는다. 동적 서명 내용/기본 발신 계정 설정과 최종 HTML 전체의 승인 binding은 아직 없다. 원격 서비스와 로컬 체크 사이 변경 경쟁도 남는다. 이 ADR은 I08 전체 충족 선언이 아니다.

## Reconsideration trigger / rollback

정책 담당자가 우선순위·시행일·과거 계약 범위를 확정하거나, 라우터 누락/불필요 재검토/지연의 실제 측정이 생기면 구조를 재평가한다. 모델 수명 종료 전 [평가 계획](../evaluation/policy-response-evaluation.md)의 작은 paired 비교를 먼저 수행한다.

이번 작업은 DB 스키마를 변경하지 않았다. 코드/프런트를 직전 버전으로 되돌려도 새 Event 종류는 기존 소비자가 무시할 수 있다. 단, 되돌리면 새 안전 검사가 사라지므로 미발송 초안 재검토와 외부 쓰기 gate 통제는 배포 책임자가 승인해야 한다. 운영 rollback/migration은 실행하지 않았다.
