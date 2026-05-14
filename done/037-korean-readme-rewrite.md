# 037 — README.md 비개발자용 한글 가이드 추가 + docs/ 디렉토리

## Why

현재 `README.md`는 개발자 onboarding 위주입니다. 비개발자가 처음 봐도
무엇을 하는 시스템이고, 어떻게 설치해서 어떻게 쓰는지 알 수 있도록
한국어 가이드를 명시적으로 추가해야 합니다. 또한 운영 중에 자주
참고할 `docs/사용법.md` / `docs/설정.md` / `docs/문제해결.md`도
필요합니다.

## What to do

1. `README.md` 상단에 **"⚡ 빠른 시작 (비개발자용)"** 섹션 추가:
   - 한 줄 요약: "이 프로그램은 무엇인가" (CLAUDE.md의 정의 활용).
   - 사전 준비물 — Python 3.11+, 인터넷, .env에 채울 자격 증명
     목록 (HubSpot 토큰, Gmail App Password, YouTube API key,
     Slack/Teams webhook 등).
   - 3단계 설치 — `scripts\setup.bat` 실행 → `.env` 채우기 →
     `scripts\run.bat` 실행.
   - 자격 증명 받는 법은 `docs/설정.md`로 링크.

2. `docs/사용법.md` 작성 (한국어 마크다운):
   - 시스템 개요 — 인바운드 / 아웃바운드 / 리포트 세 에이전트가
     무엇을 하는지, 사용자의 일상 사용 시나리오.
   - "메시지 승인 화면을 어디서 보는가" (Slack/Teams 카드).
   - "아웃바운드 prospect를 어떻게 추가하는가" (CSV 업로드 위치).
   - "리포트는 언제, 어디로 도착하는가".
   - 모든 화면에서 사용자가 누르는 버튼/하는 행동을 단계별로.

3. `docs/설정.md` 작성:
   - `.env`의 모든 변수에 대한 한국어 설명 (필수/선택, 기본값,
     얻는 법 링크).
   - 자격 증명별 발급 가이드 — Gmail App Password, YouTube Data API,
     HubSpot Private App Token, Slack Bot Token, Teams Webhook
     URL, LinkedIn `li_at` 쿠키. 각 단계 스크린샷 placeholder
     포함 (실제 이미지는 사용자가 추가).

4. `docs/문제해결.md` 작성:
   - "서버가 안 뜸" / "doctor가 FAIL" / "메일이 안 보내짐" /
     "Slack 알림이 없음" / "n8n이 실패함" 같은 흔한 증상마다
     원인 1-3개 + 해결 단계.

5. `README.md` 하단의 개발자 섹션은 유지하되, 비개발자 가이드와
   명확히 구분 (`---` 구분선 + "개발자용" 헤더).

## Acceptance criteria

- `README.md` 처음 한 화면(~50줄)에서 비개발자가 다음에 무엇을
  해야 할지 알 수 있다.
- `docs/사용법.md`, `docs/설정.md`, `docs/문제해결.md` 모두 존재
  하고 각 파일이 200줄 이상 (또는 모든 섹션이 채워짐).
- 모든 한글 문서에는 코드 블록이 있는 곳마다 PowerShell/CMD에서
  실제 실행 가능한 명령이 들어있다.

## Verify

```powershell
Get-Content README.md -TotalCount 60   # 한글 빠른 시작 보임
Get-ChildItem docs/                    # 3개 파일 존재
```
