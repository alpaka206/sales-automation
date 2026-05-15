# 082 — 컴플라이언스 회귀 테스트: outbound 메시지 body footer 검증

## Why

Polish Check 3.5: `build_footer()` 단위 테스트는 있지만, 실제
outbound 메시지 body 끝에 unsubscribe 링크 + 발신자 정보 footer가
포함되는지 검증하는 회귀 테스트가 없음.

## What to do

1. `tests/` 에 회귀 테스트 추가: outbound 에이전트가 생성하는 메시지의
   body가 footer를 포함하는지 검증.
2. 검증 항목:
   - body 끝에 unsubscribe 링크 존재
   - body 끝에 발신자 정보 (회사명, 주소 등) 존재
3. 만약 footer가 자동으로 붙지 않는 코드 경로가 있다면 해당 경로도 수정.

## Verify

```bash
pytest tests/test_compliance_footer.py -q
```
