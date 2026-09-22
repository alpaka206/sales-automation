# 로컬 실행 결과

이 파일은 초기 로컬 단계의 기록이다. 이후 사용자 지시로 수행한 실제 Gemini 평가와 추가 수정은 [후속 결과](../2026-09-21-live/results.md)가 최신이다. 아래 NOT_RUN/시험 수치는 당시 상태를 보존한다.

기준 commit: 95ec3d5c36d5eab58d7e03cfc469ea72f9332036. 커밋·stage·push·배포·실제 고객 발송·CRM 쓰기·운영 migration 없음. 기존 사용자 미추적 문서 4개를 보존했다. 입력 지시 패키지는 .gitignore에만 추가했으며 결과 문서와 분리했다.

## 최종 상태

- 전체 Python: **1655 passed, 43 skipped, 1 warning**, exit 0. [명령 메타데이터](final-python/command.json), [stdout](final-python/pytest.txt).
- 프런트엔드: **66 tests / 9 files passed**, 타입 검사·Vite build exit 0. [실행코드](frontend/commands.json), [test](frontend/test.txt), [build](frontend/build.txt).
- Ruff: **PASS**, exit 0, [로그](lint.txt).
- 실제 Gemini 평가, live HubSpot/메일, 운영 DB, 브라우저 시각 검수: **NOT_RUN**.
- 기계 요약·I01~I09·변경 분류·잔여 위험: [policy-response-result.json](../../../verification/policy-response-result.json).
- 설계/ADR/근거/평가/운영: [구조 문서](../../../architecture/policy-response-system.md)에서 연결한다.

## 재현과 중간 실패

| 실행 | 결과 | 해석 |
|---|---|---|
| baseline 최초 harness | 중단, 최종 exit 없음 | Windows asyncio loopback까지 막은 시험환경 문제; [잔여 로그](baseline/pytest.txt) |
| baseline-corrected | 1610 pass, 43 skip, exit 0 | 제품 변경 전 기준 |
| reproduced | 5 fail, 17 pass, exit 1 | 신규 회귀로 실제 결함 5개 확인 |
| tmp/policy-first-fix | 4 fail, 92 pass | patch된 Session 재사용 및 fixture DB 연결 불일치 수정 |
| tmp/policy-second-fix | 2 fail, 191 pass | 새 fixture의 필수 full_name 누락 수정 |
| tmp/policy-third-fix | 79 pass | 선택 경계/리마인더 시험 통과 |
| full-before-review-fix | 1 fail, 1645 pass, 43 skip | 과거 요약 라벨 static assertion 수정 |
| tmp/policy-last-fix | PASS | 첫 정정/요청 추출/화면 회귀 확인 |
| final-python | 1655 pass, 43 skip | 최종 전체 시험 |

tmp의 선택 실행 자료는 개발 중간 산출물이며 최종 판단은 보관된 전체 시험/JUnit에 둔다. Python uv trampoline sandbox 실행 거절은 승인된 로컬 시험 명령으로 해결했다. baseline-corrected의 command 배열은 기존 테스트의 sys.argv 변경 때문에 오염되어 있으나 실제 명령은 initial-assessment와 pytest_args에 있다. 이후 harness는 실행 전 argv를 고정한다. full-before-review-fix 디렉터리의 command.json은 원래 실행 목적지 final-python을 기록하며, 그 실행 후 결과 보존을 위해 디렉터리만 이름을 바꿨다.

모델 품질/latency/비용 개선 수치는 없다. 로컬 pytest 시간은 시험 실행 시간이지 서비스 응답시간이 아니다. 모의 발송 통과는 실제 고객 도달을 의미하지 않는다.

## 변경과 정리

- 정책 조회와 request snapshot, 대화 원문 우선 조립, 서버 소유 provenance/승인 binding을 실제 draft·approve·send 경로에 연결했다.
- 리마인더는 별도 시퀀스를 유지하고 실질 답변 판정만 분리했다.
- 이전 티켓 요약 전용 표시와 중복 TicketBlock을 제거해 세 화면을 실제 기록 + 테두리 박스로 통합했다.
- 원문 정책·DB summary·revisions·사용 중 API 호환 필드·기존 queue를 남겼다. 무관한 폴더/문서를 삭제하지 않았다.
- 정확한 변경 파일/해시는 run-manifest.json에 둔다. 사용자 원본 문서와 입력 패키지는 이 목록의 작업 산출물에서 제외한다.

## 최종 적대적 검토

검사한 공격은 고객 간 record 혼입, human_only 문서 유출, 권한/정책 version 변경, 최신 정정 잘림, reminder/test_sent 오인, 모델의 private trace 위조, 승인 후 body/to/cc/channel/status 변경, 원격 lookup 중 고객 정정, timeout 무조건 재전송, schema 오류의 고객 텍스트 로그 누출이다. 각 test ID는 기계 결과에 연결된다.

남은 결함/제약을 숨기지 않는다: 원문 의미·시간·계약 우선순위·질문별 완결성은 코드로 검증하지 않는다. legacy trace 없음, 동적 서명/링크/기본 발신 설정, 원격 동기화 지연/최종 POST 경쟁, Event의 무기한 증가/삭제 정책, context 길이 상한에 따른 원문 일부 생략은 남아 있다. 이 상태를 정책 자동 발송 준비 완료로 취급하지 않는다.
