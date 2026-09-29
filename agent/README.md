# perso-agent — 로컬 데이터 에이전트

스냅샷 CSV(`perso-data-snapshot` clone)를 **그 PC 에서** DuckDB 로 읽어 집계만 내보내는 단일
바이너리. 콘솔의 「데이터 분석」·「수주 고객」 화면이 `127.0.0.1` 로 직접 부른다. 원본도
집계도 콘솔 서버로 가지 않는다.

- 설계·조사: `docs/데이터-에이전트-설계.md`, `docs/로컬-웹-연결-조사요청.md` (로컬 문서)
- 데이터 실측: `docs/수주고객-사용현황-데이터검증-2026-09-15.md` (로컬 문서)

## 빌드

CGO 없음 — 한 대에서 세 타깃이 나온다. DuckDB CLI 는 zip 째로 `embed` 되어 첫 실행에 풀린다.

```sh
# DuckDB CLI zip 두 개를 bin/ 에 (버전은 snapshot.go 의 duckdbVersion 과 같아야 한다)
V=1.4.1
curl -L -o bin/duckdb-windows-amd64.zip   https://github.com/duckdb/duckdb/releases/download/v$V/duckdb_cli-windows-amd64.zip
curl -L -o bin/duckdb-darwin-universal.zip https://github.com/duckdb/duckdb/releases/download/v$V/duckdb_cli-osx-universal.zip

go test ./...
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/perso-agent.exe .
GOOS=darwin  GOARCH=arm64 go build -ldflags="-s -w" -o dist/perso-agent-mac-arm64 .
GOOS=darwin  GOARCH=amd64 go build -ldflags="-s -w" -o dist/perso-agent-mac-intel .
```

`.github/workflows/agent-release.yml` 이 `agent-v*` 태그에서 같은 일을 하고 Release 자산으로
올린다. 콘솔의 내려받기 버튼은 `releases/latest/download/…` 를 가리킨다.

## 실행

스냅샷 폴더 **옆**에 두고 실행한다 — `perso-data-snapshot`(git clone) · `perso-data-snapshot-main`(GitHub zip,
두 겹이어도 된다) 중 `data/manifest.json` 이 있는 첫 폴더를 찾는다. 다른 곳이면 `--repo`.

**Mac 은 `perso-agent-mac.zip` 안의 `Perso Agent.app` 이다** (2026-09-17). 맨 실행 파일은 두 번 막혔다 —
브라우저로 받으면 실행 권한이 빠져 텍스트 편집기로 열리고(「유니코드(UTF-8) 텍스트 인코딩이 적용되지
않습니다」), 권한을 붙여도 공증과 무관하게 Finder 더블클릭이 거절된다(`spctl -t exec`: `the code is valid
but does not seem to be an app`). 그래서 universal 실행 파일을 번들로 싸서 서명·공증·티켓 부착을 하고,
릴리스 워크플로가 **받은 것처럼 격리 속성을 단 zip 에서 `spctl -t exec` 이 통과해야** 릴리스한다.

앱으로 뜨면 창이 없다. 스냅샷은 앱 옆 → 홈·다운로드·데스크톱·문서 폴더 순으로 찾고(맥이 받은 앱을 임시
경로로 옮겨 실행해도 찾게), 못 찾으면 대화 상자로 알린다(`agent/app.go`). 다시 누르면 다음 포트에 하나 더 뜬다 — 떠 있는지 묻는
요청을 보내지 않기 위해서다(`tests/test_agent_stays_local.py`).
끄려면 활성 상태 보기에서 `perso-agent` 를 종료한다.

```
perso-agent.exe                      # 실행 → pull → 43110 에 뜸 → 브라우저에 콘솔이 열림 (켜 둔 동안 한 시간마다 다시 pull)
perso-agent.exe --console http://127.0.0.1:8010   # 로컬 콘솔로 시험할 때 (허용 출처 앞에 붙는다)
perso-agent.exe --unregister         # persodata:// 등록 해제 (Windows)
perso-agent.exe --no-update          # 켤 때 새 버전 확인을 건너뛴다
```

Windows 에서는 처음 실행에 `persodata://` 를 HKCU 에 등록한다(관리자 권한 불필요). 맥은 맨
바이너리로는 URL 스킴 등록이 안 되므로(.app 번들·서명 필요) 파일을 직접 실행한다.

## 스냅샷 받기 (2026-09-29 에 고침)

켤 때 한 번, 켜 둔 동안 한 시간마다 받는다. `git pull --ff-only` 를 **안 쓴다** — 그것이 clone 한 사람에게도
「안 된다」의 원인이었다(`snapshot.go` 의 `pull`):

- **받기(fetch)와 맞추기(reset)를 나눴다.** 예전에는 pull 전체가 2분 상한이라, 며칠 밀려 받을 것이 많은 PC 는
  2분에 끊기고 → git 은 받던 것을 버리고 → 다음 회차에 처음부터 → 또 끊겼다. 이제 받기는 30분까지 **뒤에서**
  돌고(켤 때는 20초만 기다리고 받아 둔 데이터로 먼저 연다), 그동안 계산은 옛 데이터로 답한다. 새 데이터로
  바꿔 끼우는 순간만 계산과 겹치지 않게 잠근다. 화면의 `pull:` 이 「진행 중」이면 끝날 때 저절로 다시 읽는다.
- **`--depth=1`**: 필요한 것은 최신 한 벌뿐이다. 일주일 밀린 복제본 실측 17.8MB → 2.5MB.
- **발행된 것과 똑같이 맞춘다**(`reset --hard`). 엑셀로 CSV 를 열어 저장했거나 끊긴 pull 이 반쯤 바꿔 둔
  파일이 있으면 `--ff-only` 는 영영 멈췄다. 추적하지 않는 파일(사람이 옆에 둔 것)은 안 건드린다.
- **끊긴 작업이 남긴 잠금**(`.git/index.lock` 등)은 10분 넘었으면 지운다. 남아 있으면 그 뒤 모든 pull 이
  실패했다(재현됨).
- **zip 으로 받은 폴더도 받는다.** 폴더 이름이 `perso-data-snapshot…` 이고 `.git` 이 없으면 그 자리에서
  git 으로 바꾼다(`git init` + 원격 + 받기). 처음 한 번 GitHub 로그인이 필요하다 — 윈도우는 Git 이 띄우는
  로그인 창, 맥은 아래. 이름이 다른 폴더(시험용)는 안 바꾼다.
- **실패 사유는 할 일로 적는다**(`pullFailure`): 로그인 필요 · 네트워크 · git 없음 · 파일이 열려 있음 · 다른
  git 작업 중. 숨은 터미널 프롬프트에서 멈추지 않게 `GIT_TERMINAL_PROMPT=0` 으로 돌린다(윈도우 로그인 창은
  그 값과 무관하게 뜬다 — Git Credential Manager 코드로 확인). 윈도우의 260자 경로 한도는
  `core.longpaths=true` 로 넘는다(깊은 폴더에서 실측).
- **맥의 GitHub 로그인**: 맥의 기본 git 은 로그인 창이 없다. 터미널에서 한 번 —
  `cd <스냅샷 폴더> && git pull` 을 하고 비밀번호 자리에 GitHub 토큰을 넣으면 키체인에 남는다. 또는
  `brew install --cask git-credential-manager` 뒤 `git pull` 하면 브라우저로 로그인한다.

시험: `pull_test.go` 가 이 PC 의 폴더를 원격으로(`file://`) 며칠 밀린 복제본 · 엑셀로 고친 파일 · 오래된/살아 있는
잠금 · zip 폴더 전환 · 시험 폴더를 잰다. GitHub 에는 안 닿는다.

## 버전과 업데이트

`perso-agent --version` 이 찍는 값은 빌드가 박은 것이다(`-X main.version=…`). 릴리스 워크플로가
태그 `agent-vX.Y.Z` 에서 떼어 넣고, 손으로 빌드하면 `dev` 다. 콘솔은 `/v1/status` 의 `version` 을
`frontend/src/lib/agent.ts` 의 `AGENT_MIN_VERSION` 과 비교한다 — **에이전트 SQL 이 내는 키가 바뀌면
그 상수를 같이 올린다.** 낡은 에이전트를 만난 화면은 「업데이트 필요」와 내려받기를 띄우고, 내려받은
파일을 옛 파일 자리에 덮어쓰면 끝이다(설정 파일도 설치도 없다). `dev` 는 언제나 최신으로 본다.

**1.5.0 부터는 스스로 올라간다** (2026-09-29, `update.go`). 켤 때 한 번 `releases/latest` 가 넘겨 주는 태그를
보고(API 를 안 쓴다 — 사무실 IP 하나가 시간당 60회를 나눠 쓴다), 새 버전이면 자기 자산을 받아
`SHA256SUMS.txt` 와 대조하고, 받은 실행 파일에 `--version` 을 물어 태그와 같은지 보고, 맥 앱은
`codesign --verify --deep --strict` 와 **개발자 팀이 지금 앱과 같은지**까지 본 뒤 제 자리에 바꿔 넣는다 —
지금 것을 `.old` 로 비키고(윈도우도 실행 중인 파일의 이름은 바뀐다) 새것을 그 이름에 두고, 같은 인자에
`--just-updated` 를 붙여 다시 띄운다. `.old` 는 새 프로세스가 지운다. 켜 둔 동안은 안 본다(다시 뜨면 토큰이
바뀌어 콘솔이 짝을 잃는다 — 사람이 켤 때 일어나야 하는 일이다).

- **어느 단계든 실패하면 지금 버전 그대로 뜬다.** 사유는 `/v1/status` 의 `update` 한 줄에 남는다
  (오프라인·사내망 · 체크섬 불일치 · 버전 불일치 · 서명 불일치 · 폴더에 쓸 수 없음).
- 맥이 다운로드 폴더의 앱을 임시 위치로 옮겨 실행한 경우(App Translocation)는 못 바꾼다 — 「응용 프로그램」
  폴더로 옮기면 그다음부터 올라간다.
- 끄려면 `--no-update`. `--just-updated` 로 뜬 프로세스는 다시 보지 않는다 — 버전을 안 올린 빌드가
  릴리스돼도 끝없이 다시 뜨지 않는다. 낮은 버전으로는 안 내려간다.
- **바깥으로 요청을 보내는 곳은 이 파일 하나다.** GET 만, 본문 없이, 이 저장소의 릴리스 주소와 거기서
  넘겨 주는 `*.githubusercontent.com` 만. 스냅샷·토큰에 닿는 이름이 그 파일에 없다
  (`tests/test_agent_stays_local.py::test_the_updater_only_downloads_our_releases`).
- 1.4.x 이하는 이 코드가 없으므로 **1.5.0 은 한 번 손으로 받아야 한다.**
- 시험: `go build -ldflags "-X main.version=1.0.0 -X main.updateFrom=http://127.0.0.1:<port>/releases"` 로
  옛것과 새것을 빌드해 가짜 릴리스 서버로 돌린다(`/releases/latest` → 302 `…/tag/agent-v1.0.1`,
  `/releases/download/agent-v1.0.1/{SHA256SUMS.txt, perso-agent.exe}`). 윈도우에서 올림 · 이미 최신 ·
  체크섬 목록 없음 · 체크섬 불일치 · 서버 불통 다섯 경우를 그렇게 쟀다(2026-09-29). **맥 경로(번들 교체 ·
  서명 대조 · `open -n`)는 아직 실기로 못 쟀다.** 윈도우 사본은 `registerScheme` 을 빈 함수로 바꿔
  빌드해야 HKCU 의 `persodata://` 를 안 건드린다.

내는 법:
```
git tag agent-v1.2.0 && git push origin agent-v1.2.0     # Actions 가 세 파일을 빌드해 Release 에 올린다
```

## 방어 (전부 실측으로 확인)

`127.0.0.1` 만 바인딩 · Host 검증(DNS rebinding) · Origin 허용목록(`*` 없음) · 프로세스마다
새 베어러 토큰 · 쿠키 없음(CSRF 표면 없음) · 화면은 SQL 을 못 보내고 지표 이름만 보냄 ·
나가는 결과는 계약 검사를 지난다(식별자 컬럼 거부 · 행 2,000 상한 · 셀 120자 상한 · 전사
집계는 5건 미만 그룹 제외).
