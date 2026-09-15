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

스냅샷 clone 폴더 **옆**에 두고 실행한다. 폴더 이름이 다르면 `--repo`.

```
perso-agent.exe                      # 실행 → pull → 43110 에 뜸 → 브라우저에 콘솔이 열림
perso-agent.exe --console http://127.0.0.1:8010   # 로컬 콘솔로 시험할 때 (허용 출처 앞에 붙는다)
perso-agent.exe --unregister         # persodata:// 등록 해제 (Windows)
```

Windows 에서는 처음 실행에 `persodata://` 를 HKCU 에 등록한다(관리자 권한 불필요). 맥은 맨
바이너리로는 URL 스킴 등록이 안 되므로(.app 번들·서명 필요) 파일을 직접 실행한다.

## 방어 (전부 실측으로 확인)

`127.0.0.1` 만 바인딩 · Host 검증(DNS rebinding) · Origin 허용목록(`*` 없음) · 프로세스마다
새 베어러 토큰 · 쿠키 없음(CSRF 표면 없음) · 화면은 SQL 을 못 보내고 지표 이름만 보냄 ·
나가는 결과는 계약 검사를 지난다(식별자 컬럼 거부 · 행 2,000 상한 · 셀 120자 상한 · 전사
집계는 5건 미만 그룹 제외).
