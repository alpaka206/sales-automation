# 09 — 통합 시스템 마스터 계획서

> 작성일 2026-05-15. 인바운드 + 아웃바운드 + 운영 UI + 패키징을 하나의 운영 시스템으로 묶는 통합 계획. plan/08 (인바운드 디테일) 의 상위 문서. 이 문서가 단일 진실 소스.

---

## 목차

- [A. 요구사항 종합](#a-요구사항-종합)
- [B. 현재 코드 상태](#b-현재-코드-상태)
- [C. 도메인별 격차 분석](#c-도메인별-격차-분석)
- [D. 아키텍처 변경](#d-아키텍처-변경)
- [E. 사용자가 빠뜨린 / 추가 결정 필요한 항목](#e-사용자가-빠뜨린--추가-결정-필요한-항목)
- [F. 리스크 / 컴플라이언스 / 비용](#f-리스크--컴플라이언스--비용)
- [G. Phase 계획 (재정렬)](#g-phase-계획-재정렬)
- [H. 시작 권유](#h-시작-권유)

---

## A. 요구사항 종합

### A-1. 운영 환경 (공통)
- **claude code CLI** 무료 LLM (Anthropic 키 없음)
- 사용자 노트북에서 BE 직접 실행
- **단일 실행 파일** 배포 (PyInstaller)
- **웹 UI** 로 모든 운영 (대시보드, 메시지 검토, knowledge_base 편집, 설정)
- claude CLI 만료 시 안내 + 상태 표시 (재로그인 자동화는 Anthropic 측 정책상 불가)

### A-2. 인바운드 (plan/08 참고)
- HubSpot 새 문의 자동 수신 (웹훅 + 폴링 fallback)
- AI 분류 / 점수 / 답장 초안 작성
- knowledge_base 자동 참조 (DB 로 이전됨)
- 사람이 웹 UI 에서 "보내기" 클릭하면 발송
- 이메일 (HubSpot 또는 SMTP) + WhatsApp 동시 발송 (key 가 있을 때만)
- 발송 후 HubSpot custom property `inbound_status: meeting_link_sent`

### A-3. 아웃바운드 (이번 명세 추가분)

**A-3-a. 잠재 고객 발굴 소스**
- 기본: **YouTube, LinkedIn**
- 추가: **Google 검색** (대학·학회·종교/사이비 등), **채용 페이지** (잡코리아·사람인의 성형외과·병원 마케팅 등)
- 확장: 사용자가 임의로 추가 가능 (예: 컨퍼런스 등록자, GitHub 스타 등)

**A-3-b. 사용자 입력 방식**
- 웹 UI 에 자연어 입력: "이런 사람들을 조사해와"
- 예: "구독자 10만 이상이고 영상 주제가 의료기기인 유튜브 채널"
- BE 가 입력을 받아서 적절한 프롬프트로 claude CLI 호출 → **browser harness 라이브러리** (browser-use 같은 AI 드리븐 브라우저) 로 크롤링
- 사용자가 Playwright 대신 AI 브라우저 라이브러리 명시 선호

**A-3-c. 수집 데이터**
- 이름 / 이메일(있으면) / 회사 / 도메인
- **소스 종류** (대학 / 유튜브 / 링크드인 / 채용공고 / 등) — 톤이 소스별로 달라짐
- **국가** — 시간대 + 번역
- **역할 / 무슨 일을 하는지**
- 가능한 추가 컨텍스트 (영상 주제, 댓글 내용, 공고 직무 등)

**A-3-d. ICP 점수**
- **도메인(소스)별로 ICP 룰을 다르게 설정 가능** — 웹 UI 에서 편집
- 점수 임계 미만은 스킵, 초과는 발송 큐로

**A-3-e. 메일 작성·발송**
- 소스별로 톤 다름 (유튜버한테 쓰는 톤 ≠ 대학교수 톤)
- **같은 도메인 동일 회사에 여러 명**: 이름만 다르게 personalize (배치 발송 처리)
- **국가별 최적 발송 시간** DB 에 저장 → recipient 국가 기준 그 시간에 발송
- **번역**: recipient 국가 언어로 자동 작성/번역
- **Gmail SMTP** 발송
- **사람이 웹 UI 에서 ok** 클릭해야 발송 (사전 검토 필수)

**A-3-f. 팔로업**
- 발송 1주일 뒤 답장 없으면 자동 팔로업 메일
- 팔로업도 다시 ok 받고 발송할지 / 자동인지 — 정해야 함 (E 섹션)

**A-3-g. 상태 파이프라인**
- `가져옴` → `메일발송` → `메일응답` → `진행중` → `won` / `lost`
- 단계 전환은 코드/이벤트로 자동, 일부는 사용자 수동

**A-3-h. 가이드 문서**
- 사용자가 나중에 추가
- knowledge_base 가 인바운드만 쓰는 게 아니라 아웃바운드 메일 작성에서도 참조하도록 확장

---

## B. 현재 코드 상태

### B-1. 인바운드
`plan/08` 의 진단 그대로. 요약:
- 초안 생성까지 ✅
- HubSpot 웹훅 수신·발송 자동·WhatsApp·상태 갱신 ❌

### B-2. 아웃바운드 (이미 부분 구현)

| 항목 | 상태 | 위치 |
|---|---|---|
| 소스 레지스트리 | ✅ 4개 등록 | `src/agents/outbound/source_registry.py` |
| manual_csv 소스 | ✅ | `sources/manual_csv.py` |
| youtube 소스 | ✅ (YouTube API v3) | `sources/youtube.py` |
| linkedin_csv | ✅ Sales Navigator export | `sources/linkedin_csv.py` |
| linkedin_comments | ✅ API or Playwright + cookie | `sources/linkedin_comments.py` |
| ICP 점수 | ✅ (LLM) | `outbound.py:_score_icp` |
| 도메인 enrichment | ✅ homepage 스크랩 + LLM 요약 | `outbound/enrichment.py` |
| 이메일 초안 | ✅ 소스별 prompt 분기 | `outbound.py:_draft_email` |
| 중복 방지 | ✅ (90일 cooldown) | `outbound.py:_is_dup` |
| Prospect 저장 | ✅ | `outbound.py:_persist_*` |
| 승인 카드 | ✅ (Slack/Teams 미연결) | `_notify.notify_approval` |

### B-3. 아웃바운드 **없음** 또는 미흡

| 항목 | 상태 |
|---|---|
| Google 검색 소스 | ❌ |
| 채용 페이지 소스 (잡코리아/사람인) | ❌ |
| 자연어 입력 → 소스 디스패처 | ❌ |
| browser-use 같은 AI 브라우저 통합 | ❌ (현재는 Playwright 직접 호출만) |
| 도메인별 ICP 룰 편집 UI | ❌ (현재 룰은 LLM 프롬프트에 하드코딩) |
| 국가별 최적 발송 시간 DB | ❌ |
| 발송 스케줄러 (시간대 큐) | ❌ |
| 다국어 번역 자동화 | ⚠️ (LLM이 language 변수만 받음, 실제 번역 품질 검증 X) |
| 발송 (Gmail SMTP) | ⚠️ (`smtp.py` 는 있는데 outbound 흐름 끝에 호출 안 함 — 승인 카드만 보냄) |
| 팔로업 자동화 | ⚠️ (`reply_check.py` 가 있는데 1주일 임계 + 자동 발송 분기 부족) |
| Gmail IMAP 답장 감지 | ❌ (HubSpot 경유면 가능, 직접 IMAP 폴링은 없음) |
| 상태 파이프라인 (`가져옴→won/lost`) | ❌ (`Prospect.status` 컬럼은 있는데 전이 로직 없음) |
| 같은 회사 멀티 personalize | ❌ |
| Bounce / unsubscribe 처리 | ❌ |

---

## C. 도메인별 격차 분석

### C-1. 자연어 소스 디스패처 (신규 핵심)

사용자가 "구독자 10만+ 의료기기 유튜브 채널" 같은 자연어를 입력하면 BE 가:
1. LLM 으로 의도 파악 → 어떤 소스(youtube / google / linkedin / job_board) 인지 결정
2. 해당 소스용 파라미터 추출 (`min_subscribers=100000`, `query="의료기기"`)
3. 적합한 소스 어댑터 호출

```python
# 신규: src/agents/outbound/dispatcher.py
class IntentRouterResult(BaseModel):
    source: Literal["youtube", "linkedin_comments", "google_search", "job_board", "manual_csv"]
    filters: dict
    confidence: float
    rationale: str

def route_intent(user_query: str, llm: LLMClient) -> IntentRouterResult:
    return llm.complete("outbound/intent_router", {"query": user_query}, schema=IntentRouterResult)
```

작업량: 3시간.

### C-2. Google 검색 소스 (신규)

대학·학회·종교/사이비·기타 자유 검색. Google Custom Search API (무료 100건/일) 또는 SerpAPI (유료) 또는 browser-use 로 직접 검색.

**추천 경로**: Google Custom Search API + 결과 페이지를 browser-use 로 더 깊이 파기.

```python
# 신규: src/agents/outbound/sources/google_search.py
class GoogleSearchSource:
    name = "google_search"
    def discover(self, filters):
        # filters: {"query": "...", "category": "university|conference|religious|other", ...}
        # 1) Custom Search API 로 후보 URL 리스트
        # 2) browser-use 로 각 URL 방문해서 contact 페이지 / about 페이지 / 사람 이름·이메일 추출
        # 3) ProspectCandidate 로 매핑
```

작업량: 5시간 + Custom Search API 키 발급 사용자 작업.

### C-3. 채용 페이지 소스 (신규)

잡코리아 / 사람인 등에서 특정 키워드 (성형외과 마케팅, 병원 SNS 등) 공고 → 회사명 · 채용 담당자 · 회사 도메인 추출.

**현실**: 잡코리아 / 사람인은 강한 anti-scraping (Cloudflare). 공식 API 는 일부 파트너 한정. browser-use 로 헤드리스 가능하지만 IP 차단 위험.

**대안**: Google Custom Search 에 `site:saramin.co.kr` 같은 site filter 거는 게 안전.

작업량: 4시간 (browser-use 통합 어렵고 시간 변동성 큼).

### C-4. browser-use 라이브러리 통합 (신규 인프라)

사용자가 명시 — Playwright 직접보다 AI 드리븐이 더 적합. 후보:

| 라이브러리 | 장점 | 단점 |
|---|---|---|
| **browser-use** (Python, popular) | Playwright 기반, LLM 으로 자연어 액션. 활발한 OSS | OpenAI 기본, claude API 도 지원하지만 **claude CLI 직접 지원 X** |
| stagehand | TS, 깔끔한 API | TypeScript 라 Python 백엔드와 별도 프로세스 |
| 직접 Playwright + claude CLI 결합 | 의존성 적음 | 우리가 다 작성해야 함 |

**문제**: browser-use 는 LLM API key 가 필요. 우리는 claude CLI 무료라 키 없음. 옵션:
- **A) browser-use + Anthropic API** — 결국 키 필요 (사용자 의도와 충돌)
- **B) browser-use + 무료 LLM (Gemini Flash 무료 티어 등)** — 가능하지만 LLM 품질 분리
- **C) 직접 만들기**: claude CLI 가 가진 WebFetch/WebSearch + Playwright 액션 큐를 우리가 조합 — 자유도 높지만 작업량 큼
- **D) browser-use 우회**: BE 가 페이지 HTML 만 Playwright 로 받아오고, claude CLI 에 HTML 던져서 "여기서 사람들 이름·이메일·역할 추출" 시키기 — **가장 현실적**

**추천 (D)**:
```python
# src/integrations/ai_browser.py (신규)
async def discover_via_ai_browser(target_url: str, instruction: str) -> list[dict]:
    """Playwright 로 페이지 가져오기 → claude CLI 로 추출 지시."""
    html = await _fetch_with_playwright(target_url)  # 우리가 작성
    structured = llm.complete("outbound/extract_prospects", 
        {"html": html[:50000], "instruction": instruction},
        schema=list[ProspectCandidate])
    return structured
```

이 방식이면 claude CLI 무료 그대로 쓰면서 AI 드리븐 추출 가능.

작업량: 6시간.

### C-5. 도메인별 ICP 룰 편집 UI

현재 ICP 점수는 LLM 프롬프트(`outbound/icp_score.md`) 안에 하드코딩. 사용자가 웹에서 수정하려면:

- DB 테이블 `icp_rules`: `source` (youtube/linkedin/...) + `criteria_md` (마크다운) + `weight` + `created_at`
- 프롬프트 렌더링 시점에 해당 소스의 `criteria_md` 를 inject
- 웹 UI 에 편집 화면

작업량: 4시간 (DB + 로더 + UI 페이지).

### C-6. 국가별 최적 발송 시간 + 스케줄러

**최적 발송 시간** (마케팅 통념):
- 평일 화-목, 오전 9-11시 또는 오후 2-3시 (현지 시간 기준)
- 단, 종교/문화별 회피 (예: 중동 금요일, 인도 다양한 휴일)

**구현**:
- DB 테이블 `country_send_windows`: `country_code` + `timezone` + `preferred_hours_start` + `preferred_hours_end` + `avoid_days`
- 초기값으로 주요국 (KR, JP, US, GB, DE, FR, SG, ID, VN, TH 등) seed
- 스케줄러: `Message` 에 `scheduled_at` 컬럼 추가, 백그라운드 워커가 `now >= scheduled_at AND status='approved'` 인 메시지 발송
- `_persist_message` 에서 recipient 국가 기준으로 다음 적절 시간 계산해서 `scheduled_at` 채움

작업량: 5시간.

### C-7. 다국어 번역

현재 `outbound/email_*.md` 프롬프트들에 `{{language}}` 변수만 받음. 실제로는 LLM 이 영어 프롬프트로 한국어 또는 다른 언어 답을 만드는 식. 품질 보장 안 됨.

**개선**:
- 프롬프트에 명시적 "respond in {{language}}" + 예시 (few-shot) 추가
- 메인 언어 5개 정도는 prompt 별도 (`email_youtube_ko.md`, `email_youtube_en.md`, ...) — overkill
- **현실적 절충**: 단일 prompt + 마지막에 "**Write in {language}. If your draft is in another language, translate it before responding.**" 강제

작업량: 2시간.

### C-8. 같은 회사 멀티 personalize

같은 도메인 (예: `acme.com`) 에 여러 사람 발굴되면, 첫 사람 외엔 personalize 정도만 다르게:
- 이름만 바꿈
- 본문은 회사 공통 가치 제안
- subject 도 약간씩 변형 ("팀과 함께 검토" 같은 변화)

**주의**: 같은 회사에 똑같은 메일 여러 통 발송하면 인하우스 알림 시스템에서 "이상 패턴" 으로 잡힘 → 도메인 차단 위험. 시간 간격 (24시간+) 둬야 함.

작업량: 2시간.

### C-9. Gmail IMAP 답장 감지

발송 1주 안에 답장 안 오면 팔로업 자동. 답장 왔는지 판별 방법:

- **A) HubSpot 경유**: 메일을 HubSpot 으로 보내면 답장도 HubSpot 에 들어옴. `get_recent_emails` 로 폴링.
- **B) Gmail IMAP 직접**: `imaplib` 으로 매시간 폴링, `In-Reply-To` 헤더로 매칭.
- **C) Gmail API + Push (Pub/Sub)**: 가장 정확하지만 설정 복잡.

**추천 (B + A 조합)**: HubSpot 으로 발송한 건 HubSpot 폴, SMTP 로만 발송한 건 IMAP 폴.

작업량: 5시간.

### C-10. 상태 파이프라인

`Prospect.status` 컬럼은 이미 있지만 전이 로직 없음. 추가:

| 상태 | 진입 조건 | 다음 상태 |
|---|---|---|
| `collected` (가져옴) | 소스에서 발굴 직후 | analyzed |
| `analyzed` | ICP 점수 + 초안 작성 완료 | sent / skipped |
| `sent` (메일 발송) | 사용자 ok 후 발송 성공 | replied / no_reply |
| `replied` (메일 응답) | 답장 감지 | in_progress |
| `in_progress` (진행중) | 사용자가 수동 마킹 또는 미팅 잡힘 | won / lost |
| `won` | 사용자 수동 마킹 | (종료) |
| `lost` | 사용자 수동 마킹 또는 명시적 거절 | (종료) |
| `skipped_lowscore`, `skipped_dup` | 점수 미달 또는 중복 | (종료) |

자동 전환 + 사용자 수동 마킹 (won/lost) 모두 UI 에서 가능해야 함.

작업량: 3시간.

### C-11. 이메일 발송 (Gmail SMTP) 한도

**Gmail 한도** (2026 기준):
- 개인 Gmail: 500/일 발송
- Workspace: 2000/일
- 신규 계정: 100/일 (점진적 증가)
- 너무 많이 보내면 24시간 정지

**아웃바운드 콜드 메일** 은 보통 운영자가 200/일 이상 시도하면 의심받음. 절대 burst 발송 X.

**해결**:
- 발송 스케줄러가 분당 X건 (예: 5건) 으로 제한
- 메일 사이 jitter (몇 십초 랜덤 대기)
- 일일 한도 추적, 한도 도달하면 다음날로 미룸

작업량: 2시간.

### C-12. WhatsApp (인바운드와 공통)

[plan/08](08_inbound_full_automation.md) C-6 그대로. 아웃바운드 첫 콜드 메시지도 WhatsApp template 발송 시도 가능 — 단 사용자가 template 사전 등록·승인 받아야 함.

---

## D. 아키텍처 변경

### D-1. 새 모듈

```
src/
├── agents/
│   ├── outbound/
│   │   ├── dispatcher.py          신규 - 자연어 → 소스 라우터
│   │   └── sources/
│   │       ├── google_search.py    신규
│   │       └── job_board.py        신규
│   └── scheduler.py                신규 - 시간대 발송 큐
├── integrations/
│   ├── ai_browser.py               신규 - Playwright + claude CLI 추출
│   ├── gmail_imap.py               신규 - 답장 감지 폴링
│   └── google_search.py            신규 - Custom Search API
├── api/
│   └── web/                        신규 - HTMX/Jinja2 UI
│       ├── routes.py
│       └── templates/
└── db/
    └── migrations/
        ├── 0003_outbound_v2.py     신규 - icp_rules, country_send_windows, knowledge_documents
        └── 0004_prospect_status.py 신규 - 파이프라인 상태 enum
```

### D-2. 환경변수 추가

```env
# Google Custom Search
GOOGLE_CSE_API_KEY=
GOOGLE_CSE_ID=

# Gmail IMAP (답장 감지)
GMAIL_IMAP_USERNAME=
GMAIL_IMAP_PASSWORD=        # App Password
GMAIL_IMAP_FOLDER=INBOX

# 발송 스케줄러
SEND_RATE_PER_MINUTE=5
DAILY_SEND_LIMIT=200

# Browser harness
BROWSER_USE_HEADLESS=true
BROWSER_USE_TIMEOUT=60

# WhatsApp (이미 .env.example 에 있음, 비활성 상태)
WHATSAPP_ENABLED=false
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
```

### D-3. DB 스키마 변화

| 테이블 | 변화 |
|---|---|
| `prospects` | `status` 컬럼에 enum 제약 추가, `scheduled_at` 컬럼 추가 |
| `messages` | `scheduled_at` 컬럼 추가, `bounced` BOOL 추가 |
| `contacts` | `whatsapp_checked_at`, `whatsapp_exists` 컬럼 |
| `icp_rules` (신규) | `source`, `criteria_md`, `weight`, `enabled`, timestamps |
| `country_send_windows` (신규) | `country_code` PK, `timezone`, `hours_start`, `hours_end`, `avoid_days` |
| `knowledge_documents` (신규) | id, title, categories(JSON), body, scope(inbound/outbound/both), created_at, updated_at |
| `outbound_intents` (신규) | 사용자 자연어 입력 기록 + 라우팅 결과 (감사용) |

---

## E. 사용자가 빠뜨린 / 추가 결정 필요한 항목

### E-1. 이메일 deliverability (콜드 메일 = 스팸 위험)

콜드 메일은 받는 사람한테 "안 시킨 메일" 이라 **GDPR / CAN-SPAM / 한국 정통망법** 위반 위험 있음. 받는 도메인의 메일 서버가 스팸으로 판정하면 **본인 도메인 전체** reputation 망가짐 (회사 메일도 못 보냄).

**필수**:
- 메일 본문에 **수신 거부 (unsubscribe) 링크** — GDPR/CAN-SPAM 의무
- 발송 도메인의 **SPF / DKIM / DMARC** 레코드 설정 (Gmail 사용 시 자동, 본인 도메인 사용 시 직접)
- **이중 옵트인** 없으면 한국 KISA 신고 가능 (B2B 는 예외 있지만 안전 X)
- 신규 도메인이면 **이메일 워밍업** 1-2주 필요 (mailwarmup.io 등)

**결정 필요**: 발신 도메인이 `@gmail.com` 개인 계정인지, `@perso.co` 같은 회사 도메인인지? 후자면 SPF/DKIM 셋업 필요.

### E-2. 가상의 받는 사람 "메일 주소" 어떻게 알아내나?

LinkedIn 댓글에서 사람을 찾았다고 **이메일 주소가 자동으로 따라오진 않음**. 옵션:

- **A) Hunter.io API** (유료, 무료 25회/월): 회사 도메인 + 이름 → 이메일 예측
- **B) Apollo.io API** (유료 + 무료 한도): 더 정확한 contact db
- **C) snov.io / lemlist** 등
- **D) 패턴 추측** (firstname.lastname@domain.com 등) — 정확도 60% 미만, bounce 다발

사용자 요구: "**도메인이 같으면 이름만 다르게 해서 나가도 될것같음**" — 이건 이미 알고 있는 이메일 가정. 그런데 LinkedIn / Google 검색에서 발굴한 사람은 도메인은 알아도 **개인 이메일은 모름**.

**결정 필요**: 발굴 결과의 처리 정책:
- (i) 이메일 모르면 발송 큐에 안 넣고 운영자가 수동으로 채우게 함
- (ii) Hunter.io 같은 유료 API 통합 (월 $49+)
- (iii) 패턴 추측 + bounce 시 자동 다른 패턴 시도

### E-3. Gmail vs 회사 도메인

Gmail SMTP 로 콜드 메일을 대량 보내면 곧 차단됨. 옵션:

- **A) 개인 Gmail 그대로 사용** (월 500건 한도, 콜드 메일은 100건 안 되면 위험)
- **B) 회사 Workspace + 별도 sending mailbox** (sales@perso.co 같은 전용 박스)
- **C) Mailgun / SendGrid / Resend** 같은 SMTP 게이트웨이 (월 5천건 무료, deliverability 좋음)
- **D) Smartlead / Lemlist / Instantly** 같은 콜드메일 전용 SaaS (월 $40+)

**MVP 추천**: B (회사 Workspace 의 sales@perso.co) + 일일 50건 한도부터 시작 → 1-2주 워밍업 후 100건.

### E-4. 자동 팔로업 — 정말 자동?

명세: "1주일 답장 없으면 팔로업 자동" — 사용자가 확인 안 하고 그냥 자동 발송?

위험: 첫 메일 무시한 사람이 팔로업 받으면 더 짜증나서 unsubscribe / spam 신고. **답장 없는 = 관심 없음** 가능성 큼.

**옵션**:
- (i) 무조건 자동 (사용자 명세 그대로)
- (ii) 1차 자동, 그 이후 (2차 팔로업)는 수동
- (iii) 다 팔로업 큐에 들어가지만 사용자가 검토 후 발송

### E-5. 사이비 종교 영업?

명세: "구글(대학, 학회, 종교-사이비도 괜찮)"

**리스크**:
- 사이비 종교 단체에 영업 메일은 brand reputation 위험 (이게 외부에 알려지면)
- 한국 정통망법 + 종교적 차별 등 민감 이슈
- 사이비 판단 기준이 모호 (누가 사이비라고 판정?)

**옵션**:
- (i) 그냥 진행 (사용자 책임)
- (ii) "religious_organization" 카테고리로 라벨만 달고 사용자가 발송 전 한 번 더 검토
- (iii) 사이비 키워드는 발굴은 하되 자동 발송 큐에서 빼고 100% 수동

### E-6. 추적 픽셀 / 링크 추적

콜드 메일 효과 측정용 (열람율, 클릭율). 단점:
- 추적 픽셀은 deliverability 깎음 (스팸 필터가 감지)
- 일부 메일 클라이언트는 이미지 자동 차단
- GDPR 상 추적은 동의 필요

**MVP 추천**: 우선 안 함. 회신 여부만 추적. 나중에 필요하면 추가.

### E-7. A/B 테스팅

같은 ICP 에 두 가지 subject / body 변형 보내고 어느 게 더 답장 받는지. **유용하지만 MVP 범위 밖**.

### E-8. 회사 도메인 vs 개인 이메일

콜드 메일 보낼 때 `john@gmail.com` 보다 `john@acme.com` 같은 회사 메일이 답장률 5배 높음. 발굴 시 회사 메일만 추출하는 필터 추가 권장.

### E-9. 받는 사람 unsubscribe / suppression 리스트

한 번 unsubscribe 하거나 spam complaint 하면 영구 차단. DB 테이블 `email_suppression` 필요.

---

## F. 리스크 / 컴플라이언스 / 비용

### F-1. 법적 리스크

| 항목 | 위험도 | 대응 |
|---|---|---|
| LinkedIn 스크래핑 | 🔴 ToS 명시 위반. 계정 영구 정지 가능 | 이미 `LINKEDIN_SCRAPING_ENABLED` 게이트로 운영자 책임 명시 |
| 한국 정통망법 (영리목적 광고성 정보) | 🟡 B2B 일부 예외. 사전 동의 미보유 시 KISA 신고 가능 | 메일 헤더에 "광고" 표기, unsubscribe 링크, 회사 정보 명시 |
| GDPR (EU 거주자 대상) | 🔴 위반 시 매출 4% 또는 €2천만 | "정당한 이익" 근거 + opt-out 즉시 처리. EU 타겟이면 신중 |
| CAN-SPAM (미국) | 🟡 발신자 정보·unsubscribe·물리 주소 필수 | 메일 footer 표준화 |
| 사이비 종교 영업 | 🟡 평판 리스크 | 발송 전 한 번 더 검토 단계 |

### F-2. 운영 리스크

| 항목 | 위험 | 대응 |
|---|---|---|
| 발신 도메인 reputation 망가짐 | 🔴 회사 메일 전체 못 보냄 | 별도 sending subdomain (`mail.perso.co`) 사용 |
| Gmail SMTP 한도 초과 | 🟡 24h 정지 | rate limit + 일일 카운터 |
| Browser harness 가 차단당함 | 🟡 IP 차단, 캡차 | rate limit + residential proxy 또는 손으로 풀기 |
| LLM 환각 (잘못된 회사 정보 인용) | 🟡 받는 사람 거부감 | 발송 전 사람 검토 (이미 정책) |
| 같은 회사에 burst 발송 | 🟡 받는 회사 메일 필터가 도메인 차단 | 시간 간격 + 일일 회사당 한도 |

### F-3. 비용

| 항목 | 월 비용 (MVP 수준) |
|---|---|
| Supabase Postgres (500MB) | $0 |
| Render BE (free tier) — 우리는 노트북이라 0 | $0 |
| Cloudflare Tunnel | $0 |
| YouTube Data API v3 | $0 (10,000 단위/일, 검색 100 단위) |
| Google Custom Search API | $0 (100건/일 무료, 그 이상 $5/1000) |
| HubSpot 개발자/테스트 계정 | $0 |
| Gmail SMTP | $0 (개인) / 사용자 Workspace 비용은 별도 |
| Hunter.io (선택) | $0 (25/월) ~ $49+ |
| Meta WhatsApp Business | $0 (1000 대화/월) |
| Anthropic API | $0 (claude CLI 무료) |
| **합계** | **$0** 또는 lead enrichment 쓰면 ~$50/월 |

---

## G. Phase 계획 (재정렬)

| Phase | 영역 | 추정 | 가치 |
|---|---|---|---|
| **Phase 1** | 인바운드 받기 자동화 | 5h | 새 문의 자동 처리 시작 |
| **Phase 2** | 인바운드 발송 + 상태 + WhatsApp 통합 | 4h | 응답 보내기 자동화 |
| **Phase 3** | knowledge_base DB 이전 | 3h | UI 편집 사전 작업 |
| **Phase 4** | 아웃바운드 인프라 (스케줄러 + 상태 + 발송) | 8h | 발굴-검토-발송 코어 |
| **Phase 5** | 아웃바운드 자연어 입력 + 새 소스 (Google / 채용) | 10h | 다양한 발굴 채널 |
| **Phase 6** | browser-use 통합 (Playwright + claude CLI 결합) | 6h | AI 기반 크롤링 |
| **Phase 7** | 다국어 + 국가별 발송 시간 | 5h | 글로벌 영업 핵심 |
| **Phase 8** | Gmail IMAP 답장 감지 + 자동 팔로업 | 5h | 후속 대응 자동화 |
| **Phase 9** | 웹 UI (대시보드, 메시지 검토, KB 편집, ICP 룰 편집, 자연어 입력 폼) | 15h | 사용자 인터페이스 통합 |
| **Phase 10** | PyInstaller 단일 실행 파일 | 4h | 비개발자 배포 |
| **Phase 11** | 컴플라이언스 (unsubscribe, suppression, footer) | 4h | 법적 안전망 |

**총 약 70시간** (claude CLI 환경, 본인 노트북 기준)

### G-1. 추천 순서

1. **Phase 1–3** (인바운드 마무리, 12시간) — 가장 적은 노력으로 가장 큰 가치
2. **Phase 11 컴플라이언스 일부 먼저** (4시간) — 발송 시작 전 안전망 (unsubscribe + footer)
3. **Phase 4** (아웃바운드 코어, 8시간) — 발송 자동화 기반
4. **Phase 9 부분** (대시보드 + 메시지 검토만, ~6시간) — 사용자가 검토할 수 있어야 함
5. 여기서 **운영 시작 가능 (소규모)** — 25시간 누적
6. **Phase 7** (다국어 + 발송 시간) — 글로벌 영업 시작
7. **Phase 5, 6** (자연어 + 새 소스 + browser) — 발굴 확장
8. **Phase 8** (답장 감지 + 팔로업) — 운영 자동화
9. **Phase 10** (패키징) — 마지막에 배포 단순화

---

## H. 시작 권유 + 결정 요청

전체 70시간이라 한 세션에 다 못 합니다. 다음 결정만 주시면 첫 작업 들어가겠습니다:

### H-1. 발신 도메인

- (A) 개인 Gmail (devrel.365@gmail.com) 그대로 사용 (월 100건 제한 권장)
- (B) 회사 Workspace 의 sales@perso.co 같은 별도 박스 (워밍업 1-2주 필요)
- (C) Mailgun/SendGrid 같은 SMTP 게이트웨이 무료 티어 사용

### H-2. 이메일 발굴 정책 (LinkedIn / Google 에서 사람만 찾았을 때 메일 모르는 경우)

- (i) 메일 모르면 큐에 안 넣음, 운영자 수동 입력
- (ii) Hunter.io 유료 (월 $49) 통합으로 자동 추정
- (iii) 패턴 추측 + bounce 시 다음 패턴 시도

### H-3. 자동 팔로업 자동도

- (i) 무조건 자동 (1주일 후 발송)
- (ii) 1차 팔로업은 자동, 2차부터는 사용자 검토
- (iii) 모든 팔로업이 검토 큐에 들어감

### H-4. 사이비 종교 발굴 처리

- (i) 진행 (운영자 책임)
- (ii) 라벨만 달고 발송 전 한 번 더 검토
- (iii) 발굴만 하고 발송 안 함

### H-5. browser-use vs 자체 Playwright+claude

- (A) browser-use (LLM API key 필요 — Anthropic 종량제 등) — claude CLI 무료 정책과 충돌
- (B) 자체 Playwright + claude CLI (D-4 의 D 안) — 무료 유지, 작업량 +2h

### H-6. 시작 phase

- (a) Phase 1 부터 순서대로 (인바운드 마무리 먼저)
- (b) Phase 4 부터 (아웃바운드 코어부터, 발송은 일단 수동)
- (c) Phase 9 부터 (웹 UI 먼저, 다른 기능 없이 화면만)
- (d) 다 결정해서 한 번에 다 진행 (단, 70시간이라 멀티 세션 필요)

위 6개 답주시면 그에 맞춰 Phase 1 부터 들어가겠습니다.
