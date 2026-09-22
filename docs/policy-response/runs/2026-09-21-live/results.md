# 실제 Gemini 확인과 후속 수정 — 2026-09-21

결론: **로컬 구현·회귀 검증 완료, 실제 Vertex 개발셋 평가 수행, 정책 의미 정확성은 PARTIAL**. 고객 발송·CRM 쓰기·배포·운영 migration·커밋은 실행하지 않았다. 앞선 [로컬 단계](../2026-09-21-local/results.md)의 Gemini NOT_RUN은 이 후속 기록으로 갱신한다.

## 확인 범위와 데이터

사용자가 후속 메시지에서 실제 확인을 지시한 뒤 실행했다. Vertex 인증이 작동하며 실제 모델은 gemini-2.5-flash / gemini-2.5-pro, global이다. 승인이 없다는 이유로 평가를 보류한 상태가 아니다.

현재 운영 정책 확보는 실패했다. PostgreSQL은 read-only transaction/연결 timeout을 설정해 일반 실행과 권한 확장 실행을 각각 시도했으나 연결 시간 초과였다. 알려진 서비스 URL에 설정된 Basic 인증으로 정책 조회 GET을 시도했으나 HTTP 401이었다. 로그인된 브라우저도 없었다. 과거 로컬 백업은 현재 model_access가 없어 human_only 여부를 추정해 전송하지 않았다. 이 때문에 **현재 회사 정책/실제 고객 문의의 정확도는 NOT_RUN**이다.

대신 [합성 fixture](../../../../tests/fixtures/policy_response_live.json)의 정책 6개(1개는 human_only sentinel)와 문의 14개를 사용했다. 조건·기존 계약·특별 약정·미확인 사실·첫/후속·정정·복수 질문·injection·영어를 포함한다. 기간/조건은 가상이며 운영 정책으로 등록하지 않았다. 기대사항은 최초 호출 전에 작성했지만 개발 과정에서 전체를 보았으므로 독립 holdout이 아니다.

`evaluate_policy_response.py`는 쓰기/worker 설정을 프로세스 안에서 끄고 임시 SQLite를 설정한 뒤 실제 classify→router→draft→finalize를 호출한다. 모델 호출은 실제 Vertex, 저장은 disposable SQLite이다. HubSpot handle의 CRM 보강, 운영 API 전송, 큐 전체를 실행하지 않는다. source export는 읽기 전용이다. raw 입력·응답은 Git 제외 `tmp/policy-eval-2026-09-21`에 있고 공개 산출물에는 합성 답변/판정/호출 메타데이터만 남겼다.

## 비교와 판정

주 비교는 baseline 56회와 candidate 56회: 14개 문의 × router/all 두 방식 × 2반복이다. 반복 2에서 순서를 뒤집었다. 같은 fixture/정책 snapshot/모델/출력 4000토큰/Pro 추론 128을 사용한다. all은 라우터 모델 호출만 생략하고 기존 허용 후보 전체 fallback을 사용한다.

baseline은 **앞선 로컬 안전 경계 개선 후, live 발견 결함 수정 전**이다. Git 기준 commit 그대로의 코드가 아니다. candidate는 답변 요소 조합/인용·기간·상태 검사/제한 재작성/후속 지시 수정이다. 여러 변경을 묶어 비교했으므로 각각의 독립 효과를 증명하지 않는다. baseline의 전체 코드 snapshot은 고정하지 못했고 prompt/schema hash와 raw 입력 및 결과는 남아 있다. candidate에는 실행 당시 주요 코드 hash가 있다.

112개 결과를 구현 에이전트 한 명이 가상 원문 및 기대사항과 직접 대조했다. 독립 전문가·맹검 평가가 아니다. 조건 충족과 중대한 근거 없는 주장이 없으면 PASS, 명확한 위반은 FAIL, 의미가 애매하면 REVIEW_REQUIRED로 남겼다. 초안 차단은 FAIL이며 분모에서 제외하지 않는다. “중대 오류”는 처리/기간/사실/조건/권위의 잘못된 단정이다. 정규식 검사 결과를 의미 점수로 사용하지 않았다.

| 버전 / 문서 방식 | 유효 답변 / 전체 | 생성 성공 | 의미 재검토 | 중대 오류 출력 / 전체 | 실제 API 호출 | p50 / p95 초 |
|---|---:|---:|---:|---:|---:|---:|
| baseline / router | 20/28 | 28/28 | 1 | 5/28 | 86 | 4.835 / 5.907 |
| baseline / all | 16/28 | 28/28 | 1 | 8/28 | 58 | 3.984 / 4.828 |
| candidate / router | 20/28 | 24/28 | 2 | 2/28 | 92 | 6.094 / 11.546 |
| candidate / all | 23/28 | 26/28 | 1 | 2/28 | 65 | 5.196 / 11.406 |

따라서 **기존 router 경로의 유효 답변 수는 개선되지 않았으며**, 관측 중대 오류는 줄었지만 초안 차단과 지연이 늘었다. all 분기는 이 작은 개발셋에서 더 많은 유효 답변을 냈지만 기준선에서는 반대였다. 현재 회사 정책 크기/충돌/예외를 확보하지 못해 전면 all 전환 근거로 삼지 않는다. 실제 파일/문서 수가 다를 때의 우열은 NEEDS_MEASUREMENT이다.

주 비교의 입력/표시 출력 토큰은 baseline 124,729 / 10,914, candidate 168,673 / 22,787이다. 출력에는 원문 인용/답변 요소 및 실패한 시도도 포함한다. adapter가 별도 thinking 토큰을 기록하지 않으므로 청구 토큰 합계나 정확한 금액으로 표현하지 않는다. p95는 28개 시도의 nearest-rank, 실패 지연 포함이다. 같은 14문의를 반복한 종속 표본이며 신뢰구간/모집단 정확도는 추정하지 않았다.

판정 원본: [adjudications.json](adjudications.json). 합성 답변 전체: [reviewed-synthetic-answers.json](reviewed-synthetic-answers.json). 재현 가능한 집계: [summary.json](summary.json). 수치가 판정자 판단에 의존하므로 별도 검토자가 원문을 보고 수정할 수 있게 했다.

## 발견 → 수정 → 남은 실패

1. baseline S02/S14: 원문에 없는 5~10일/1~2영업일, 환불 접수·진행 중 생성. 인용 membership, 숫자 기간, 일부 실행 상태 검사와 재작성 1회 추가. 전체 문서 입력만으로 해결되지 않았다.
2. plan-smoke S02: answer_points에 있던 14일 조건이 별도 body에서 사라짐. 자유 body 재서술을 삭제하고 `compose_answer`로 직접 연결한다. 후속 응답을 원래 미해결 질문까지 연결하는 지시도 수정했다.
3. candidate S05/S06: 다운로드 이력을 실제 확인했다고 단정, 새 계약의 조건을 과거 계약에 이식, 승인된 서명 계약이라는 조건 중 승인을 생략. **남아 있는 높은 위험**이다. 인용 존재는 적용/권위/충분성 검증이 아니다.
4. candidate S14-router-2: 미확인 완료 상태를 “아니다”로 바꿈. 주 비교 완료 뒤 부정 완료 단정도 검사에 포함하고 해당 문장 변형의 회귀 테스트를 추가했다. 최종 수정 후 `final-negative-recheck`에서 2/2 생성했고 해당 허위 상태 표현은 관측하지 않았다. 한 출력은 현재 완료 상태에 직접 답하지 않는다는 의미 검토 여지가 남는다. 이 2건을 56건 수치에 덮어쓰지 않았다.
5. 작업 큐가 일반 오류를 8회 재시도해, 의미 재작성 제한 후에도 반복 생성할 수 있었다. `DraftEvidenceError`는 이미 내부 재작성이 끝난 오류이므로 job을 dead로 남겨 자동 반복을 막았다. 메시지는 기존 draft_failed 표시로 검토·수정/수동 재생성한다. 고객에게 거절 답변을 자동 생성하지 않는다.

주 비교 이후 바뀐 제품 코드는 부정 상태 검사와 큐의 terminal 분류다. 최신 상태에서 전체 56개 live 비교는 **NOT_RUN**이고 S14 2개만 live 재확인했다. 전체 로컬 시험은 최신 코드에서 다시 통과했다. 부분 재확인을 전체 실평가 통과로 부르지 않는다.

## 탐색 실행과 전체 호출

[experiment-inventory.json](experiment-inventory.json)에 모두 포함했다. smoke 2, baseline 56, candidate-smoke 6, reasoning-smoke 6, plan-smoke 6, composed-smoke 6, followup-smoke 2, candidate 56, unknown-state-recheck 4, final-state-recheck 2, final-negative-recheck 2: **148개 시도, 실제 provider 414회**, 관측 입력 425,050토큰 / candidate 출력 53,972토큰. 오류/실패 재시도도 포함한다. 추론 토큰/청구 금액은 미수집이다.

reasoning-smoke는 Pro 추론을 1024로 높인 탐색이다. S02 누락/허위 진행 상태가 남고 지연이 늘어 기본값을 바꾸지 않았다. 인용만 추가한 smoke, 중복 body를 둔 plan-smoke, 조합만 적용한 composed-smoke의 실패도 삭제하지 않았다. 구조가 단계별로 달라 정식 단일 변수 ablation/holdout 성능으로 비교하지 않는다.

주 비교 candidate exit 1은 56개 실행 중 6개 `DraftEvidenceError`가 있었기 때문이다. 스크립트 실행 완료와 답변 성공은 구분한다. 최종 차단의 raw 모델 출력은 당시 저장하지 않아 마지막 검사 코드/false-positive 여부는 판정하지 못했다. 이를 실제 정책 오류가 확실해서 모두 올바르게 막았다고 주장하지 않는다.

## 로컬 검증·명령

- 최신 전체 Python: **1678 passed / 43 skipped / 1 warning**, pytest 36.52초, exit 0. [로그](final-python-complete/pytest.txt), [명령](final-python-complete/command.json), [JUnit](final-python-complete/junit.xml).
- 중간 시험: 1672 pass 후, 부정문 새 회귀에서 1 fail / 1674 pass. “아니/아닙” 활용형 처리가 빠졌던 검사 결함을 수정했다. 1675 pass 재확인 뒤 추가 문장 변형 및 큐 시험까지 포함한 최신 전체 실행이 위 1678이다. 중간 실패도 보존했다.
- Ruff 최신 소스/테스트/세 스크립트 통과. git diff --check 통과. 모델 호출 후 프런트 코드는 변경하지 않았으며 앞선 66 tests / tsc·Vite build 통과를 유지한다. 브라우저 시각 검수는 NOT_RUN이다.
- 실제 고객 발송/CRM 쓰기/운영 migration/배포/커밋은 NOT_RUN이다.

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_policy_response.py --preflight
.\.venv\Scripts\python.exe scripts/evaluate_policy_response.py --live --dataset tests/fixtures/policy_response_live.json --arms router all --repeats 2 --max-calls 220 --output tmp/policy-eval-new
.\.venv\Scripts\python.exe scripts/summarize_policy_evaluation.py --raw-root tmp/policy-eval-2026-09-21 --reviews docs/policy-response/runs/2026-09-21-live/adjudications.json --output docs/policy-response/runs/2026-09-21-live/summary.json
.\.venv\Scripts\python.exe scripts/check_policy_response.py --result-dir tmp/policy-check tests -q -ra
.\.venv\Scripts\ruff.exe check --no-cache src tests scripts/check_policy_response.py scripts/evaluate_policy_response.py scripts/summarize_policy_evaluation.py
```

live 명령은 실제 유료 모델 호출이다. max-calls는 금액 상한이 아니다. baseline 폴더가 없는 새 평가에 과거 판정 파일을 재사용하지 않는다. 새 답변을 실제 대조한 판정 파일이 필요하다. 집계 도구의 답변 공개는 저장소 합성 fixture와 정책 hash가 일치하는 자료에만 허용한다.

## 연결과 다음 판별 실험

[구조](../../../architecture/policy-response-system.md), [후속 ADR](../../../adr/2026-09-21-grounded-draft-composition.md), [운영 절차](../../../operations/policy-response-runbook.md), [기계 결과](../../../verification/policy-response-result.json), [변경 파일/최종 hash](run-manifest.json)를 연결했다. DB migration은 없다. 원래 사용자 문서/운영 설정/리마인더 시퀀스를 보존했다.

다음 최소 실험은 접근 가능한 **현재 승인 원문 snapshot**과 담당자가 확정한 적용 시점·계약 우선순위, 숨긴 실제 문의를 사용한 router/all 비교다. 먼저 S05/S06의 사실·권위 오류와 S14의 상태 답변, 과잉 차단/상담사 수정시간을 독립 검토한다. 모델 수명 종료 전 대체 모델도 같은 입력으로 비교한다. 현재 개발셋 수치로 운영 정확도나 자동 발송 준비를 선언하지 않는다.
