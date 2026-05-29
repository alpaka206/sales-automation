# todo/ 안내

`todo/`는 아직 처리하지 않은 작업 명세를 모아두는 곳입니다. 파일명 번호는
작업 순서(작을수록 먼저)를 나타냅니다. 작업을 끝내면 해당 파일을 `done/`으로
옮겨 히스토리로 보관합니다.

각 todo 파일은 보통 다음을 담습니다:
- **Why** — 왜 하는지
- **What to do** — 구체적 작업 내용
- **Acceptance criteria** — 완료 판정 기준
- **Verify** — 검증 명령 (보통 `pytest` 또는 `python -m ...`)

새 todo를 끼워 넣고 싶다면:
- 기존 번호 사이에 `008a-...`, `008b-...` 식으로 추가하거나
- 그냥 끝에 새 번호를 붙이면 됩니다 (의존성은 acceptance criteria에 적어주세요).

## 현재 남은 작업

- `087-browser-harness-toggle.md` — 아키텍처 의사결정 대기 중(BLOCKED).

설계 배경은 `plan/` 폴더, 회사 규칙은 `company_rules/` 폴더를 참고하세요.
