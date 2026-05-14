# Sales Automation — Ralph Loop Project

내부용 세일즈 자동화 시스템. 인바운드 처리 / 아웃바운드 발굴 / 보고를 자동화하고, 발송은 사람 승인 후에만 나갑니다.

## 어떻게 만들어지나

이 프로젝트는 **Claude Code + ralph_loop**로 자동 개발됩니다.
- `PROMPT.md`가 Claude의 매 iteration 마스터 프롬프트
- `CLAUDE.md`가 프로젝트 컨텍스트
- `plan/`에 사양, `todo/`에 잘게 쪼갠 작업, `done/`에 완료된 작업
- `company_rules/`에 회사 내부 규칙 (톤, 금지어, 시그니처 등) — md 파일로 자유롭게 추가

## 퇴근 전 5분 세팅

```bash
cd %USERPROFILE%\Desktop\sales-automation

# 1) 환경변수 채우기 (.env 만들고 필요한 키 넣기)
copy .env.example .env
notepad .env   # API 키 입력

# 2) git 초기화 (ralph_loop가 commit을 찍기 때문에 필수)
git init
git add -A
git commit -m "chore: initial scaffold"

# 3) ralph_loop 시작 (Windows)
scripts\ralph_loop.bat

# 또는 git-bash / WSL
bash scripts/ralph_loop.sh
```

ralph_loop가 돌기 시작하면 `claude` CLI가 매 iteration `PROMPT.md`를 읽고 `todo/`의 다음 작업을 골라 코드를 작성합니다. 아침에 `done/` 폴더와 `logs/ralph_history.log`를 확인하면 진행 상황이 보입니다.

## 필요한 것

### 필수 (없으면 ralph_loop 멈춤)
- `claude` CLI 로그인 완료 (Claude Code 설치 + `claude login`)
- Python 3.11+
- Git

### 나중에 채워도 되는 것 (스텁으로 시작)
- HubSpot Private App Token — 인바운드/아웃바운드 실제 동작용
- SMTP 자격 또는 HubSpot 메일 권한 — 발송용
- YouTube Data API Key — 아웃바운드 YouTube 소스
- Slack Bot Token / Teams Webhook — 승인 워크플로
- Anthropic API Key — 있으면 자동으로 API 모드, 없으면 `claude -p` CLI 모드

`.env.example` 참고.

## LLM 호출 방식

API 키 없이도 동작합니다. `LLM_PROVIDER=claude_cli` (기본값)이면 `claude -p "..."` 를 subprocess로 호출합니다. API 키가 생기면 `.env`에 `ANTHROPIC_API_KEY=` 채우고 `LLM_PROVIDER=anthropic_api` 로 바꾸면 끝.

## 무료 호스팅 옵션

`plan/07_free_hosting_guide.md` 참고. 요약:
- n8n: 로컬 `npx n8n` 또는 Render/Railway 무료 티어
- BE (FastAPI): Render free, Fly.io free, Railway free trial
- DB: SQLite 로컬 → 필요시 Supabase 무료 (500MB Postgres)

## 폴더 가이드

| 폴더 | 용도 |
|---|---|
| `plan/` | 설계 사양 — Claude가 읽고 따른다 |
| `todo/` | 다음에 할 작은 작업들 (번호 순서대로) |
| `done/` | 완료된 작업 (히스토리) |
| `company_rules/` | 회사 내부 규칙 (톤, 시그니처, 금지어) |
| `src/` | 실제 소스코드 |
| `n8n_workflows/` | n8n 워크플로 JSON export |
| `scripts/` | `ralph_loop.bat`, `init_db.py` 등 |
| `tests/` | pytest 테스트 |
| `data/` | 로컬 SQLite 파일 (gitignored) |
| `logs/` | ralph 히스토리 + 앱 로그 (gitignored) |

## ralph_loop 멈추기

`Ctrl+C` 한 번이면 현재 iteration 끝나고 다음 iteration 진입 전에 멈춥니다. 다시 시작하면 마지막 상태에서 이어집니다.

## 문제가 생기면

1. `logs/ralph_history.log` 끝부분 확인 → 어떤 작업에서 막혔는지
2. `todo/<번호>-<제목>.md` 옆에 `BLOCKER.md`가 생겼는지 확인
3. 그 작업의 acceptance criteria를 더 작게 쪼개서 todo로 다시 넣으면 됨
