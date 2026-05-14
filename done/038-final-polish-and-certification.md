# 038 — 최종 폴리시: 데드 코드 정리 + 커버리지 확인 + RALPH_DONE 발행

## Why

todo 028-037을 모두 완료한 뒤, 시스템 전체를 한 번 점검하고 사용자가
실제 사용 가능한 상태인지를 확인합니다. 통과하면 `RALPH_DONE.md`를
만들어 ralph loop을 종료시킵니다.

## What to do

1. **데드 코드 정리**:
   - `ruff check --select F401,F841 src tests` 결과 0건이 될 때까지
     미사용 import / 미사용 변수 제거.
   - `# TODO:` / `# FIXME:` / `XXX:` 코멘트가 남아 있으면 각각을
     해결하거나 GitHub issue 스타일로 별도 todo로 옮긴 뒤 코멘트
     제거 (todo로 옮기는 경우 이 todo는 미완료 상태로 두고 새 todo
     처리 후 다시 시도).

2. **테스트 전수 통과**:
   - `pytest -q` 결과 0 fail, 0 error.
   - `pytest --cov=src --cov-report=term-missing -q`로 모듈별
     커버리지 확인. < 70%인 모듈이 있으면 그 모듈에 대한 새 todo를
     `todo/`에 만들고 이 todo는 미완료로 둠.

3. **사전점검**:
   - `python -m src.cli doctor` 결과가 모두 PASS (또는 optional
     WARN). FAIL 있으면 원인 해결.

4. **n8n workflow 검증**:
   - `n8n_workflows/*.json` 파일들이 JSON syntactically valid한지
     `python -c "import json; [json.load(open(p)) for p in
     glob.glob('n8n_workflows/*.json')]"` 등으로 확인.

5. **최종 인증서 발행** — `RALPH_DONE.md` 작성:
   ```markdown
   # 폴리시 완료 — Sales Automation

   - 완료 일시: YYYY-MM-DD
   - 최종 커밋: <git rev-parse HEAD>

   ## 시스템 요약 (한국어 한 문단)
   <CLAUDE.md 기반으로 무엇이 가능한지 정리>

   ## 다음 단계
   - 사용자: README.md → 빠른 시작 섹션
   - 운영자: docs/사용법.md, docs/문제해결.md
   ```

6. 커밋: `chore: 폴리시 완료 — 폐쇄 루프 종료 (#038)`.

7. `.ralph_stop` 파일을 repo root에 만들어 다음 iteration이
   ralph_loop.bat를 즉시 종료하도록 함.

## Acceptance criteria

- `pytest -q` 통과.
- `ruff check src tests` 0건.
- `python -m src.cli doctor` 모두 PASS/WARN (FAIL 없음).
- `RALPH_DONE.md` 존재.
- `.ralph_stop` 존재.
- 커밋 메시지는 한국어.

## Verify

```
pytest -q
ruff check src tests
python -m src.cli doctor
type RALPH_DONE.md
dir .ralph_stop
```
