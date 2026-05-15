# Reading order for todo/

ralph_loop는 파일명 번호 순서대로 작업합니다. 번호가 작을수록 먼저 처리됩니다.

`done/`로 이동된 파일은 작업 완료를 의미합니다. 진행 중에 막힌 경우 같은 폴더에 `BLOCKER.md`가 생기며, ralph가 다음 iteration에 재시도합니다.

새 todo를 끼워 넣고 싶다면:
- 기존 번호 사이에 `008a-...`, `008b-...` 식으로 추가하거나
- 그냥 끝에 새 번호를 붙이면 됩니다 (의존성은 acceptance criteria에 적어주세요).

## 현재 todo 목록 (Phase 1–11, plan/09_integrated_system.md 참고)

### Phase 1 — 인바운드 받기 자동화
1. 039 — cloudflared 터널 안내 + 자동 시작 스크립트
2. 040 — HubSpot 웹훅 실페이로드 매핑 + 서명 검증
3. 041 — 인바운드 10분 폴링 워커
4. 042 — HubSpot 에서 메일 본문 실제 fetch

### Phase 2 — 인바운드 발송 + 상태 + WhatsApp
5. 043 — HubSpot custom property `inbound_status` 자동 갱신
6. 044 — WhatsApp Cloud API 풀 구현 (게이팅)
7. 045 — 이메일 + WhatsApp 이중 발송 디스패처

### Phase 3 — knowledge_base DB 이전
8. 046 — knowledge_documents 테이블 + 기존 .md import
9. 047 — knowledge.py 로더 DB 기반

### Phase 4 — 아웃바운드 코어
10. 048 — SMTP 멀티 제공자 안내 (Outlook/Brevo/SendGrid)
11. 049 — country_send_windows 테이블 + 주요국 seed
12. 050 — 발송 큐 워커 (deferred send)
13. 051 — SMTP rate limit + 일일 한도 + jitter
14. 052 — Prospect 상태 파이프라인 enum

### Phase 5 — 아웃바운드 새 소스 + 자연어
15. 053 — 자연어 입력 → 소스 디스패처
16. 054 — Google Custom Search 소스
17. 055 — 채용 페이지 소스 (사람인/잡코리아)
18. 056 — LinkedIn 프로필 페이지 이메일 추출
19. 057 — 일반 페이지 footer 이메일 추출

### Phase 6 — Browser harness
20. 058 — AI 브라우저 (Playwright + claude CLI 결합)
21. 059 — 기존 소스에 AI 브라우저 통합

### Phase 7 — 다국어 + 발송 시간
22. 060 — 다국어 메일 프롬프트 강화
23. 061 — 아웃바운드 메시지 국가별 발송 시간 적용

### Phase 8 — 답장 감지 + 팔로업
24. 062 — Gmail IMAP 답장 감지
25. 063 — 1주 무답 자동 팔로업 큐

### Phase 9 — 웹 UI
26. 064 — 웹 UI 인프라 (Jinja2 + HTMX + Tailwind CDN)
27. 065 — 대시보드 (메시지 + 카운트)
28. 066 — 메시지 상세 + 발송 버튼
29. 067 — knowledge_base CRUD UI
30. 068 — ICP 룰 편집 UI + DB
31. 069 — 아웃바운드 자연어 입력 + 발굴 결과 검토
32. 070 — 설정 페이지 (헬스 + claude CLI 상태)

### Phase 10 — PyInstaller 패키징
33. 071 — build.spec + Windows .exe 빌드
34. 072 — 첫 실행 .env 자동 생성 마법사

### Phase 11 — 컴플라이언스
35. 073 — Unsubscribe + footer + suppression
36. 074 — GDPR/CAN-SPAM/정통망법 텍스트

---

전체 약 70시간 분량. 각 todo 는 1-3시간. ralph_loop 가 순서대로 처리.

처리 끝나면 Polish mode 가 자동 발동:
- 코드 품질 (TODO/lint/dead code)
- 테스트 커버리지 <70%
- 비개발자 readiness (README, setup.bat, docs)
- 최종 인증 (pytest 그린, doctor 그린)

Polish 끝나면 `RALPH_DONE.md` 생성 + `.ralph_stop` 으로 종료.
