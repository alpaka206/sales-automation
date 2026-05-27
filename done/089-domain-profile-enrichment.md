# 089 — 인바운드 이메일 도메인 → 회사·서비스 자동 분석

## Why

지금 인바운드가 들어오면 `Contact.company`(HubSpot 자유텍스트) 만 보고
LLM 이 분류·스코어링·드래프트를 한다. 문제는:

- HubSpot 의 `company` 가 비어있거나 오타·약어인 경우가 흔하다.
- 도메인이 개인 메일(gmail/naver/...)이 아닌 회사 도메인이면, 그 도메인
  자체가 **누가 보냈는지** 에 대한 가장 강력한 시그널인데 지금은 활용 안 됨.
  (`src/agents/inbound.py:_base_score` 에서 personal 도메인 페널티 -10
  / 기업 도메인 +15 만 하고 끝.)
- 같은 도메인에서 두 번째 인바운드가 와도 매번 LLM 이 "이 회사가 뭐 하는
  회사인지 모름" 상태에서 답변. 회사 컨텍스트가 누적되지 않는다.

**목표**: 이메일 도메인에서 회사를 식별하고 "무슨 서비스를 하는 회사"
인지 한 번 분석해서 캐시하고, 그 컨텍스트를 `_classify` / `_score_adjust`
/ `_draft_reply` 세 프롬프트에 모두 주입한다. 같은 도메인은 캐시 재사용.

## 입력 / 출력 (실제 예시 금지 — 도메인은 런타임에 결정)

- 입력: `info["email"]` 에서 추출한 도메인 (`_domain_from_email`).
- 분석 소스: ① 도메인 홈페이지 GET → `<title>`, `<meta name="description">`,
  `<meta property="og:description">` 추출 ② 위 추출 결과 + 도메인을 LLM 에
  넘겨 구조화된 회사 프로파일 생성.
- 출력: `DomainProfile { company_name, industry, services, target_market,
  size_hint, confidence, source, notes }` — DB 캐시.

## What to do

1. **DB 스키마**
   - `src/db/migrations/0015_domain_profiles.py` 신규.
     ```sql
     CREATE TABLE domain_profiles (
       domain VARCHAR(255) PRIMARY KEY,
       company_name VARCHAR(255),
       industry VARCHAR(128),
       services TEXT,
       target_market VARCHAR(128),
       size_hint VARCHAR(64),
       confidence VARCHAR(16) NOT NULL,  -- "high" | "medium" | "low"
       source VARCHAR(32) NOT NULL,      -- "llm+homepage" | "llm_only" | "manual"
       homepage_title TEXT,
       homepage_description TEXT,
       homepage_fetch_status VARCHAR(32),  -- "ok" | "timeout" | "http_4xx" | "http_5xx" | "blocked" | "skipped"
       notes TEXT,
       analyzed_at DATETIME NOT NULL,
       updated_at DATETIME NOT NULL
     );
     CREATE INDEX ix_domain_profiles_industry ON domain_profiles (industry);
     ```
   - `src/db/models.py` 에 `DomainProfile` 모델 추가 (위와 동일 컬럼).
     `idempotent` 마이그레이션 패턴은 `0014_conversation_ticket_id.py` 참고.

2. **개인 도메인 판별 유틸**
   - `src/agents/inbound.py` 의 `_PERSONAL_DOMAINS` 를 `src/common/
     domains.py` 로 이동해 공유 가능하게 만들고, 다음을 추가:
     - gmail, naver, daum, yahoo, hotmail, outlook, icloud, kakao, hanmail,
       nate, gmx, proton, protonmail, yandex, qq, 163.
   - `is_personal_domain(domain: str) -> bool`.
   - `is_role_address(local_part: str) -> bool` — `info|sales|hello|
     contact|admin|support|hr|recruit|noreply|no-reply` 등은 별도 신호로
     기록 (분석은 그래도 진행).

3. **홈페이지 fetch 모듈**
   - `src/integrations/web_fetch.py` 신규 (이미 있으면 확장):
     - `fetch_homepage_meta(domain: str, *, timeout: float = 5.0) ->
       HomepageMeta` — `https://<domain>` GET, 실패 시 `http://` fallback.
     - `User-Agent` 는 `Mozilla/5.0 (compatible; SalesBot/1.0; +<COMPANY_NAME>)`
       (config 의 `COMPANY_NAME` 사용).
     - 응답 크기 1MB 캡, `text/html` 만 처리, 비-HTML 은 status="blocked".
     - `<title>` / `<meta name="description">` / `<meta property="og:
       description">` / `<meta name="keywords">` 만 정규식 또는
       BeautifulSoup4 로 추출. HTML 전체 LLM 에 넘기지 않음 (cost+노이즈).
     - 결과: `HomepageMeta { title, description, og_description, keywords,
       status: Literal["ok","timeout","http_4xx","http_5xx","blocked"] }`.
   - SSRF 가드: `domain` 이 localhost/private IP/메타데이터 endpoint
     (`169.254.169.254`)면 status="blocked" 로 즉시 리턴. 호스트 해석
     결과도 사설망이면 차단.

4. **도메인 분석 모듈**
   - `src/agents/domain_enrichment.py` 신규:
     - `analyze_domain(domain: str, *, llm: LLMClient, hint_company: str |
       None = None) -> DomainProfile` — DB 캐시 lookup → 없으면 fetch +
       LLM 호출 → DB upsert → 반환.
     - `force_refresh: bool = False` 옵션 (수동 재분석용).
     - `_MAX_AGE_DAYS = 90` — 그보다 오래된 프로파일은 자동 재분석 후보로
       표시(`stale=True` 반환), 다만 매 인바운드마다 강제 재분석은 X.
       재분석은 별도 CLI / 스케줄러에서 트리거하도록 hook 만 노출.

5. **새 LLM 프롬프트**
   - `src/llm/prompts/inbound/analyze_domain.md` 신규 (`output: json`):
     - 입력 변수: `{{domain}}`, `{{hint_company}}`, `{{homepage_title}}`,
       `{{homepage_description}}`, `{{homepage_keywords}}`,
       `{{fetch_status}}`.
     - 출력 스키마: `company_name`, `industry`(자유 텍스트, 예: "B2B SaaS
       — observability"), `services`(1-3 문장), `target_market`, `size_hint`
       (`"startup"|"smb"|"midmarket"|"enterprise"|"unknown"`), `confidence`
       (`"high"|"medium"|"low"`), `notes`(LLM 이 알아둘 만한 정황 한 줄).
     - 규칙: 홈페이지 정보가 없거나 status≠"ok" 이면 `confidence` 는
       `"low"` 로 강제. 모르면 추측 금지 → `confidence="low"`,
       `company_name=null`, `notes="insufficient signal"`.

6. **InboundAgent 통합**
   - `src/agents/inbound.py:_fetch_contact` 마지막 단계 (HubSpot deals
     조회 뒤) 에 도메인 enrichment 호출 추가:
     - 이메일이 있고 `is_personal_domain(domain) == False` 이고
       `settings.INBOUND_DOMAIN_ENRICHMENT_ENABLED` 일 때만 실행.
     - 실패해도 `_fetch_contact` 전체는 항상 성공 — 예외는 warning 로그
       1줄로 삼키고 `info["domain_profile"] = None` 으로 두기.
     - 결과는 `info["domain_profile"]` (dict 또는 None) 으로 저장.
   - `_build_enrichment_context` 확장: `domain_profile` 이 있으면
     ```
     Sender's domain profile (auto-analyzed):
     - domain: <domain>
     - inferred company: <company_name> (confidence: <confidence>)
     - industry: <industry>
     - services: <services>
     - target market: <target_market>
     - size hint: <size_hint>
     - notes: <notes>
     ```
     블록을 enrichment_context 에 추가.
   - `_base_score` 에 추가 시그널: `size_hint in {"midmarket","enterprise"}`
     이면 +5, `industry` 가 ICP 와 매칭되면 별도 점수 (단, ICP 매칭은
     기존 `icp_rules` 와 충돌 안 하게 코멘트로 표시만 — 점수 변경은
     `_score_adjust` LLM 에 맡긴다).

7. **설정 토글**
   - `src/common/config.py:Settings` 에 추가:
     ```python
     INBOUND_DOMAIN_ENRICHMENT_ENABLED: bool = True
     INBOUND_DOMAIN_HOMEPAGE_FETCH: bool = True   # off=LLM-only
     INBOUND_DOMAIN_FETCH_TIMEOUT_SECONDS: float = 5.0
     INBOUND_DOMAIN_REANALYZE_DAYS: int = 90
     ```
   - `.env.example` 에 위 4개 추가 (주석 포함).

8. **수동 재분석 CLI**
   - `scripts/reanalyze_domain.py <domain> [--force]` — 운영 중 한 도메인의
     프로파일을 강제로 다시 분석할 때 사용. `done/` 의 패턴 따라 짧게
     쓰면 됨.

9. **웹 UI 노출 (얕게)**
   - `src/api/main.py` 의 메시지 상세 페이지 (`/messages/{id}`) 또는
     컨택트 detail 에 `DomainProfile` 이 있으면 작은 카드로 보여주기.
     edit 기능까지 만들 필요는 없음 — 1차는 read-only.

10. **테스트**
    - `tests/agents/test_domain_enrichment.py`:
      - 개인 도메인 → 분석 안 함 (`analyze_domain` 이 LLM 호출 0회).
      - 캐시 hit → LLM/fetch 둘 다 호출 0회.
      - 캐시 miss + homepage fetch ok → LLM 1회 호출, DB row 1건 생성.
      - homepage fetch 실패 (timeout/4xx/5xx) → LLM 은 그래도 호출되되
        `source="llm_only"`, `confidence="low"` 로 저장.
      - SSRF 가드: `localhost`, `127.0.0.1`, `169.254.169.254` 입력 →
        status="blocked", LLM 호출 안 함.
    - `tests/integrations/test_web_fetch.py`:
      - httpx mock 으로 title/og:description 추출 검증, 비-HTML
        content-type 처리, 응답 크기 캡, redirect 체이닝 max 3회.
    - `tests/agents/test_inbound_domain_integration.py`:
      - 인바운드 처리 시 `enrichment_context` 에 domain profile 블록이
        포함되는지 (stub LLM 으로 프롬프트 렌더 결과 검증).
      - enrichment 가 실패해도 인바운드는 끝까지 처리되어 `Message`
        row 가 생성되는지.

## Acceptance criteria

- 새 회사 도메인의 인바운드가 들어오면 `domain_profiles` 테이블에 row 1건이
  생기고, `homepage_fetch_status="ok"` 케이스에선 `source="llm+homepage"`,
  `confidence` 가 `"high"` 또는 `"medium"`.
- 같은 도메인의 두 번째 인바운드는 `domain_profiles` SELECT 만 발생하고
  LLM `analyze_domain` 호출은 0회 (Event 테이블 `kind="llm_call"` 카운트로
  확인 가능).
- 개인 도메인 (gmail/naver 등) 인바운드는 `domain_profiles` row 생성도
  LLM 호출도 없다.
- 도메인 fetch 가 timeout 으로 실패해도 인바운드 처리는 끝까지 가서
  `messages.status="pending_approval"` row 가 생긴다.
- draft_reply 프롬프트 렌더 결과 (Event 로그 또는 테스트) 에 도메인
  프로파일 블록이 포함된다.
- 기존 인바운드 회귀 없음 (`pytest tests/agents/test_inbound*.py` 그린).

## Verify

```powershell
# 마이그레이션
.venv\Scripts\python.exe -m src.db.migrate

# 단위 + 통합
.venv\Scripts\python.exe -m pytest tests/agents/test_domain_enrichment.py tests/integrations/test_web_fetch.py tests/agents/test_inbound_domain_integration.py -q

# 회귀
.venv\Scripts\python.exe -m pytest tests/agents/test_inbound.py tests/agents/test_inbound_ticket.py -q

# 실연동: 회사 도메인 가진 컨택트로 새 ticket 만들고
.venv\Scripts\python.exe scripts\run_inbound_ticket.py <TICKET_ID>
# → 로그에 "Domain profile analyzed: <domain> industry=<...>" 1회
# → 같은 도메인 두 번째 ticket → "Domain profile cache hit: <domain>"

# 수동 재분석
.venv\Scripts\python.exe scripts\reanalyze_domain.py <DOMAIN> --force
```

## Risks

- **외부 fetch**: 일부 사이트는 봇 차단 (Cloudflare 403, 1020) 또는 매우
  느림. 5초 타임아웃 + LLM-only fallback 으로 처리하되, 차단 사이트가
  많아지면 fetch 끄고 LLM-only 모드 (`INBOUND_DOMAIN_HOMEPAGE_FETCH=false`)
  로 운영 가능해야 함.
- **LLM 환각**: 도메인만 보고 LLM 이 잘못된 회사 정보를 만들 수 있음.
  → 프롬프트에 "모르면 confidence=low + company_name=null" 명시,
  UI 에서 `confidence` 와 함께 노출해 운영자가 인지하게 함.
- **SSRF**: 사용자 메일 도메인을 그대로 fetch 하므로 내부 네트워크 hit
  위험. 사설망/메타데이터 IP 가드 필수 (위 3-SSRF 항목).
- **캐시 staleness**: 회사가 pivot 하거나 인수되면 90일 캐시가 거짓이
  됨. 우선은 90일 자동 stale 표시만 하고, 강제 갱신은 수동 CLI 로 위임.
- **PII / 컴플라이언스**: 메일 도메인은 PII 가 아니지만, 분석 결과를
  HubSpot 으로 역동기 하지 말 것 (1차는 로컬 DB 만). 후속 todo 로 분리.
- **비용**: 새 도메인마다 LLM 호출 1회. 캐시가 잘 들으면 점진적으로
  0 에 수렴. 초기 며칠은 호출량 모니터링 필요.

## Dependencies

- 088 (HubSpot Ticket 인바운드 지원) — 머지 완료. 089 는 ticket / 일반
  인바운드 둘 다 적용된다.
