# Reading order for todo/

ralph_loop는 파일명 번호 순서대로 작업합니다. 번호가 작을수록 먼저 처리됩니다.

`done/`로 이동된 파일은 작업 완료를 의미합니다. 진행 중에 막힌 경우 같은 폴더에 `BLOCKER.md`가 생기며, ralph가 다음 iteration에 재시도합니다.

새 todo를 끼워 넣고 싶다면:
- 기존 번호 사이에 `008a-...`, `008b-...` 식으로 추가하거나
- 그냥 끝에 새 번호를 붙이면 됩니다 (의존성은 acceptance criteria에 적어주세요).

## 현재 todo 목록 (요약)
1. 001 — Python 프로젝트 부트스트랩
2. 002 — 설정 + 로깅
3. 003 — DB 모델 + init 스크립트
4. 004 — LLM 클라이언트 (claude_cli 우선)
5. 005 — HubSpot 클라이언트
6. 006 — FastAPI 스켈레톤
7. 007 — 인바운드 에이전트 (실구현)
8. 008 — 아웃바운드 소스 레지스트리 + manual_csv
9. 009 — 아웃바운드 에이전트 (dedup + 점수 + draft)
10. 010 — 승인 + 발송 (HubSpot/SMTP)
11. 011 — 답장 확인 + 팔로업
12. 012 — 리포트 에이전트
13. 013 — YouTube 소스
14. 014 — LinkedIn CSV 소스
15. 015 — n8n 워크플로 export
16. 016 — E2E 스모크 테스트
17. 017 — `python -m sales doctor` 사전점검 CLI

이 todo들이 다 끝나면 ralph가 알아서 plan/을 다시 읽고 새 todo를 추가합니다.
