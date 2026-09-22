# 근거 장부 — 2026-09-21 확인

구현은 [설계](../architecture/policy-response-system.md), 판단은 [ADR](../adr/2026-09-21-policy-response-boundaries.md), 측정은 [평가](../evaluation/policy-response-evaluation.md)에 연결한다. 코드/로컬 실험과 공식 문서 확인은 서로 다른 근거다.

| ID | 출처와 확인 내용 | 설계에 사용한 범위 | 미확인 |
|---|---|---|---|
| E01 | 기준 commit 95ec3d5, inbound.py/knowledge.py/prompts.py/approval.py/send_worker.py 직접 확인 | 최소 구조 보강, 기존 승인/리마인더 유지 | 운영 환경·실제 정책 DB 내용 |
| E02 | [baseline](../policy-response/runs/2026-09-21-local/baseline-corrected/pytest.txt), [5개 결함 재현](../policy-response/runs/2026-09-21-local/reproduced/pytest.txt), [최종 시험](../policy-response/runs/2026-09-21-local/final-python/pytest.txt) | 실제 버그와 회귀 구분 | 실제 고객 정확도·지연·비용 |
| E03 | [Google structured output 공식 문서](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/control-generated-output) | response_schema 및 지원 subset과 로컬 JSON 파싱을 구분 | 현재 프로젝트/모델 조합의 live 호환성 |
| E04 | [Google 데이터 보존 공식 문서](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention) | 학습 사용과 저장/abuse monitoring/search grounding 로그는 다른 조건임을 확인 | 회사 프로젝트의 실제 약정·설정·보존 기간 |
| E05 | [HubSpot Conversations 가이드](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide), [Help Desk COMMENT 변경 공지](https://developers.hubspot.com/changelog/upcoming-breaking-change-conversations-api-help-desk-and-comments) | MESSAGE 이메일 발송과 내부 COMMENT 변경을 구분 | 테스트 portal 실제 scope/인박스/발신계정 권한 |
| E06 | [Google 모델 lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions) | 저장소 기본 gemini-2.5-pro/flash의 대체 평가 필요성 | 환경변수 override, 대체 모델 품질·가격 |

Google의 기존 cloud.google.com/vertex-ai/generative-ai URL은 확인 시 위 Gemini Enterprise Agent Platform 문서로 redirect되었다. API surface를 Developer API로 바꿨다는 뜻이 아니다. 코드 adapter는 `src/llm/providers/gemini_vertex.py`의 google-genai Vertex 호출이다. 이번 작업은 provider, 지역, 모델 설정을 변경하지 않았다. File Search/caching을 새로 도입하지 않아 다른 surface의 지원 조건을 가져와 구현 근거로 삼지 않았다.

Structured output은 형식 제어다. 현재 adapter의 JSON 출력 요청 + Pydantic 파싱/제한 재시도는 provider response_schema 사용과 다르다. 어느 방식도 원문에 없는 예외·허위 완료 주장·정책 적용 시점 오류를 자동으로 증명하지 못한다. native schema는 별도 contract 시험 후 선택한다.

데이터 보존 문서는 학습 미사용과 저장 미사용이 같지 않음을 보여준다. grounding 등 기능별 보존 조건은 별개다. 실제 계정 설정을 조사하거나 ZDR 충족을 인증하지 않았다. 초기 로컬 단계에는 모델을 호출하지 않았고, 사용자의 후속 실평가 지시 후 합성 개발 자료로 실제 Vertex를 호출했다. [후속 평가 기록](../policy-response/runs/2026-09-21-live/results.md)이 최신 상태다.

HubSpot 공지는 2026-09-23부터 Help Desk 스레드의 COMMENT 생성이 바뀌며 CRM Notes 사용을 안내한다. 현재 고객 답장은 MESSAGE이므로 이를 이유로 이메일 경로를 CRM Notes로 바꾸지 않았다. 가이드 예시의 recipient actorId를 그대로 복사하지 않은 이유는 저장소에 해당 portal의 actor 조합 거절과 address 방식에 대한 회귀 근거가 있기 때문이다. 이번 세션에서 live API 재확인은 하지 않았다.

확인 시 모델 수명 표는 gemini-2.5-pro/flash의 종료일을 2026-10-20으로 표시했다. 후속 평가 preflight 및 실제 호출에서 이 모델 ID와 Vertex/global을 확인했다. 즉시 모델명만 바꾸면 같은 정책 입력에서도 품질이 바뀌므로 대체 모델 paired 평가가 필요하다. 이 날짜는 이후 공식 문서 변경 시 다시 확인해야 한다.

## 확인된 실패와 가설

| 문제 | 근거 수준 | 조치 |
|---|---|---|
| 긴 요약 때문에 최근 정정이 빠짐 | baseline 회귀 테스트 실패로 재현 | 최근 원문 우선 예산 |
| reminder/test_sent가 실질 응답 판정을 흐림 | 각각 회귀 실패 재현 | 실제 답변 기준 통일 |
| 정책/대화 DB 실패가 빈 맥락이 됨 | 각각 회귀 실패 재현 | 초안 실패/재검토 |
| 다른 시점의 정책 선택/생성 | 코드 분석 + mutation 테스트 | 같은 snapshot 사용, 전후 검증 |
| 승인 후 내용/근거 변경 | 코드 분석 + 신규 경계 테스트 | 승인 hash/전송 전 검증 |
| router가 숨은 예외를 놓칠 수 있음 | 합성 live 비교에서 전체문서도 동일 계열 오류. 실제 정책에서는 미검증 | 라우터 유지, 실제 원문 비교 필요 |
| 많은 Event/정책 전체 hash 비용 | 구조상 가설, p95 미측정 | 데이터량별 DB/latency 측정 |

입력 패키지의 가상 환불 기간·계약 예외는 운영 정책으로 등록하지 않았다. 운영 seed와 설정도 변경하지 않았다.

E07: 실제 live 출력에서 허위 환불 기간·업무 진행 상태·고객 주장 승격을 관측했다. E08: 인용/추론 한도 확대/중복 body 생성의 한계를 탐색했고, 답변 요소 조합과 제한 검사를 도입했다. E09: 운영 정책 DB는 read-only 연결 시간 초과, 설정 인증을 사용한 조회 API는 401이었다. 과거 로컬 백업은 현재 model_access 경계를 확인할 수 없어 외부 평가에 쓰지 않았다. 근거·반박·비용은 [후속 ADR](../adr/2026-09-21-grounded-draft-composition.md)과 [실행 기록](../policy-response/runs/2026-09-21-live/results.md)에 연결한다.
