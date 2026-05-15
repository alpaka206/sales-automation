# 039 — cloudflared 터널 안내 + 자동 시작 스크립트

## Why

노트북에서 띄운 FastAPI 가 HubSpot 웹훅을 받으려면 외부 HTTPS URL 이 필요. cloudflared 무료 터널이 정답. 비개발자가 손쉽게 띄울 수 있게 안내 + 자동화.

## What to do

1. `scripts/tunnel.bat` 신규 — `cloudflared tunnel --url http://localhost:8000` 실행. 출력에서 `https://xxx.trycloudflare.com` URL 만 추출해서 콘솔에 큼지막하게 표시 + `data/last_tunnel_url.txt` 에 저장.
2. `scripts/run_with_tunnel.bat` 신규 — `run.bat` + `tunnel.bat` 동시 실행 (별도 창).
3. `docs/배포.md` 또는 `docs/사용법.md` 에 "외부 접근 URL 만들기" 섹션 추가 — cloudflared 설치 (`winget install Cloudflare.cloudflared`), 실행, HubSpot 웹훅에 URL 등록.
4. `scripts/setup.bat` 끝에 cloudflared 설치 여부 체크 + 미설치 시 안내.

## Acceptance criteria

- `scripts\tunnel.bat` 실행하면 cloudflared 가 트래픽을 `localhost:8000` 으로 포워딩하고 URL 출력.
- URL 이 `data/last_tunnel_url.txt` 에 저장됨.
- `docs/배포.md` 에 cloudflared 설치/사용 안내 한 섹션.

## Verify

```powershell
# cloudflared 설치되어 있다는 전제
scripts\tunnel.bat
# 다른 창에서:
Get-Content data\last_tunnel_url.txt
curl https://<output URL>/healthz
```

## Risks / open questions

- 무료 임시 터널 URL 은 재시작 시 바뀜. 고정 URL 원하면 Cloudflare 계정 + DNS 셋업 별도 안내. 일단 임시 URL 로 충분.
