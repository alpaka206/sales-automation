# 검증과 모델 평가 — 2026-09-21

로컬 회귀 검증은 **PASS**, 실제 Gemini 합성 개발셋 평가는 수행했고 의미 정확성은 **PARTIAL**이다. 사용자의 후속 실평가 지시 후 실제 Vertex 414회/148개 시도를 실행했다. 운영 정책 DB timeout/API 401 때문에 현재 회사 정책·실제 고객 정확도는 NOT_RUN이다. [최신 live 결과와 판정 원본](../policy-response/runs/2026-09-21-live/results.md)에 비교·실패·한계를 기록했다.

## 실행 결과

| 구분 | 결과 | 근거 |
|---|---|---|
| 기존 전체 Python 기준 | 1610 passed / 43 skipped / exit 0 | [baseline](../policy-response/runs/2026-09-21-local/baseline-corrected/pytest.txt) |
| 수정 전 신규 결함 재현 | 5 failed / 17 passed / exit 1 | [reproduced](../policy-response/runs/2026-09-21-local/reproduced/pytest.txt) |
| 초기 변경 후 Python | 1655 passed / 43 skipped / exit 0 | [local final](../policy-response/runs/2026-09-21-local/final-python/pytest.txt) |
| live 발견 결함 수정 후 최신 Python | 1678 passed / 43 skipped / exit 0, pytest 36.52초 | [latest final](../policy-response/runs/2026-09-21-live/final-python-complete/pytest.txt) |
| 프런트엔드 | 9 files / 66 tests passed / exit 0 | [vitest](../policy-response/runs/2026-09-21-local/frontend/test.txt) |
| 타입 검사·Vite build | exit 0 | [build](../policy-response/runs/2026-09-21-local/frontend/build.txt) |
| Ruff | All checks passed / exit 0 | [lint](../policy-response/runs/2026-09-21-local/lint.txt) |
| 실제 Gemini | 주 비교 112개 + 탐색/후속 36개 시도 | [실제 결과](../policy-response/runs/2026-09-21-live/summary.json), [전체 호출](../policy-response/runs/2026-09-21-live/experiment-inventory.json) |
| 운영 정책 평가/실제 발송 | NOT_RUN | 정책 조회 timeout/401; 고객 발송은 미허가 |
| 브라우저 시각 검수 | NOT_RUN | SSR 렌더링 검사와 타입/build까지만 실행 |

43 skips는 기존 test_lazy_imports.py의 서드파티 모듈 제외다. Python 경고 1개는 기존 Starlette/httpx deprecation이다. build에는 런타임 제공 /static/*.css 경로와 500kB 초과 번들 경고가 남는다. PowerShell이 stderr를 NativeCommandError 형태로 로그에 기록했지만 실제 npm exit code는 0이다. 경고를 제거했다고 주장하지 않는다.

중간 전체 시험에서 1 failed/1645 passed가 발생했다. 기존 테스트가 과거 “기존 대화 요약” 문구를 문자 비교했고 새 표기 “파생 대화 요약·원문 우선”으로 바뀐 탓이다. 요구사항에 맞게 assertion을 수정한 뒤 전체 재실행했다. 초기 loopback 차단 harness 실패와 fixture 수정 과정은 [실행 기록](../policy-response/runs/2026-09-21-local/results.md)에 별도로 남겼다.

## 재현 명령

저장소 루트에서:

```powershell
.\.venv\Scripts\python.exe scripts/check_policy_response.py --result-dir tmp/policy-check tests -q -ra
.\.venv\Scripts\ruff.exe check --no-cache src tests scripts/check_policy_response.py
cd frontend
npm.cmd test
npm.cmd run build
```

합성 fixture가 DB를 구성하고 모델/transport를 mock한다. 실제 _draft_reply → _finalize_draft → approve → sender 경로도 포함한다. 임시 SQLite migration 테스트는 실행되지만 운영 DB migration은 실행하지 않는다. 새 스키마 migration은 없다.

## 무엇을 입증했는가

5개 재현 실패가 최종 전체 시험에서 통과했다: 긴 파생요약 때문에 최신 정정이 빠지는 문제, reminder의 기준선 이동, test_sent 오인, 대화 조회 실패의 빈 이력 처리, 정책 조회 실패의 빈 규칙 처리. 동일한 재현 입력에서 제어 흐름이 바뀐 근거다.

추가 시험은 정책 body/access/version/scope 변경, 승인 후 본문·제목·수신자·CC·계정·상태 변경, 고객 정정, 원격 주소 조회 중 정정, private provenance 위조, 로그 raw 응답 누출, history 고객 혼입, 실제 기록 중복, 기존 리마인더·delivery_unknown 회귀를 다룬다.

**형식 검증/테스트 통과 개수는 고객답변 정확도가 아니다.** 후속 live 비교는 단일 구현 에이전트가 합성 원문과 112개 결과를 대조했다. 중대 오류·유효 답변·차단·모호한 표현을 별도로 집계했다. 자연스러움과 실제 상담사 수정시간의 독립 정량 평가는 NOT_RUN이다. 개발 fixture는 holdout이 아니며 두 CLI를 독립 실행하지 않았으므로 어느 CLI가 우수하다는 결론도 없다.

C01~C50은 공개 설계 시나리오다. [기계 결과](../verification/policy-response-result.json)의 acceptance_coverage에서 관련 경계 시험과 해당 시나리오 자체의 NOT_RUN을 구분한다. 예제 전체를 실행 가능한 benchmark로 구현했다거나 50건 모두 통과했다고 보고하지 않는다. 가상 기간/예외를 정책 seed로 등록하지 않았다.

## 실제 원문·독립 평가의 다음 프로토콜 — PLANNED

1. 정책 담당자가 승인한 원문/시행일/계약 우선순위와 데이터 담당자가 외부 전송을 승인한 fixture를 고정한다. 원본 hash, 모델 ID, Vertex API/SDK/지역, prompt/schema/config, 토큰 상한, 예산·중단 조건을 기록한다. 정책과 고객 데이터의 권한 범위를 별도로 확인한다.
2. 가장 작은 비교는 **현 flash 라우터 + 선택 문서** 대 **같은 허용 문서 전체 + 같은 생성 모델**이다. 새 인덱스/그래프/추가 validator 없이 “검색 누락” 가설을 가른다. 정답 근거를 generation 입력에 몰래 추가하는 oracle 비교는 별도 진단으로만 둔다.
3. 개발셋은 첫 답변/짧은 후속/이미 제공한 정보/고객 정정/복수 질문/미확정 사실/예외·과거 계약으로 층화한다. 동일 티켓·유사 정책의 파생 사례는 분할 간 분리한다. 최종 holdout은 소유자가 보관하고 구현자가 보지 않는다. 승인 전 실제 세트 크기/예산은 null이다.
4. 같은 입력·문서 snapshot·모델·출력 예산으로 paired 실행하고 각 입력을 반복해 변동을 본다. 반복 수는 예산 승인 시 고정한다. 평가자는 문장 순서를 무작위화해 어느 분기인지 모르게 채점한다.
5. 지표: 중대한 정책 오류(예외/조건/시간/권위/허위 완료), 질문별 유효 답변율, 답할 수 있는 부분의 누락, 불필요 질문/이관, 사실 정정 반영, 한국어 자연스러움, 검토 수정시간, 입력/출력 토큰·비용, p50/p95 지연. 분모·적용 가능 모집단·반복·신뢰구간을 각각 기록한다. 모든 답을 보류하는 기준선도 함께 보고한다.
6. gate 임계치는 정책/운영 담당자가 사전 합의한다(**현재 미확정**). 권한 누출/미승인 송신은 관측 시 중단 대상이다. 표본에서 오류 0건이어도 위험 0이라고 표현하지 않는다. 안전 통과와 유효 답변율/운영 부담을 함께 비교한다.

`check_policy_response.py`는 외부 차단 로컬 회귀용이다. 별도 `evaluate_policy_response.py --live`는 실제 유료 Gemini 호출을 임시 DB에서 실행한다. 자동 고객 전송 옵션은 없다. `summarize_policy_evaluation.py`는 사람/검토자의 명시적 판정 파일을 집계하며 모델 판정을 자동 생성하지 않는다. 실행 방법·데이터 hash·최종 코드와 주 비교의 차이·탐색 실패는 [후속 실행 기록](../policy-response/runs/2026-09-21-live/results.md)을 따른다.
