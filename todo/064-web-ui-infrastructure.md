# 064 — 웹 UI 인프라 (Jinja2 + HTMX + Tailwind CDN)

## Why

비개발자가 localhost:8000 에서 모든 운영을 할 수 있는 웹 인터페이스. 인바운드 메시지 검토·발송, knowledge_base 편집, 아웃바운드 자연어 입력 + 발굴 결과 확인 등.

## What to do

1. `src/api/web/` 디렉토리 신규:
   - `routes.py` — 모든 웹 UI 라우트 등록.
   - `templates/base.html` — Tailwind CDN, HTMX CDN, 한글 폰트.
   - `templates/partials/` — 재사용 컴포넌트.
2. FastAPI 의 `Jinja2Templates` + `StaticFiles` 설정.
3. 인증: `localhost` 만 허용 (외부 접근 차단). `INTERNAL_API_TOKEN` 미들웨어 우회 분기 추가 (웹 UI 는 쿠키 기반).
4. `src/api/main.py` 에 `app.include_router(web_router)` 등록.
5. `GET /` → 빈 대시보드 placeholder (다음 todo 에서 채움).

## Acceptance criteria

- 브라우저에서 `http://localhost:8000/` 접속 시 "Sales Automation" 타이틀 + Tailwind 스타일 적용된 빈 페이지.
- HTMX 가 CDN 으로 로드됨.

## Verify

```powershell
# 서버 띄운 상태에서
curl http://localhost:8000/
```
