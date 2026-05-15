# 08 — 인바운드 풀 자동화 계획서

> 작성일 2026-05-15. 사용자 요구사항을 받아서 현재 코드 상태와 격차를 1:1로 매핑한 문서. 작업 추정치는 claude CLI 환경에서 본인 노트북 단독 운영을 전제로 한다.

---

## A. 요구사항 정리

원본 요청을 단계별 명세로 분해:

1. **운영 환경**
   - claude code CLI 무료 사용 (Anthropic 키 없음)
   - 사용자가 직접 백엔드 띄움
   - **단일 실행 파일** 같은 배포 (Spring `.tar`/`.jar` 느낌)
   - **진행 확인/상호작용 웹 UI**
   - claude CLI **언제든 재로그인 가능한 메커니즘** (전용 로그인 키 등)

2. **인바운드 트리거**
   - HubSpot "New" 상태 새 문의가 들어오면
   - 가능하면 **HubSpot 웹훅** 으로 받기
   - 안 되면 **로컬 cron 10분 폴링** fallback

3. **처리 파이프라인**
   - AI 가 메일 내용 분석
   - 내부 규정 문서 참고
   - HubSpot 이메일 엔진으로 답장 발송
   - **+** 보낸 사람 전화번호로 **WhatsApp 검색 → 있으면 다이렉트 메시지도 발송** (이중 채널)
   - 발송 완료 후 HubSpot 상태 `new` → `meeting link sent`

---

## B. 현재 상태 진단 (코드 1:1 매핑)

### ✅ 이미 동작하는 부분

| # | 항목 | 코드 위치 | 비고 |
|---|---|---|---|
| 1 | claude CLI LLM 호출 | `src/llm/providers/claude_cli.py` | UTF-8 + 코드펜스 처리 안정화됨 |
| 2 | 사용자가 BE 띄우기 | `scripts/run.bat` → `uvicorn src.api.main:app` | 그대로 동작 |
| 3 | AI 분류·점수·초안 작성 | `src/agents/inbound.py` `InboundAgent.handle()` | 검증 완료 |
| 4 | 내부 규정/안내 문서 참조 | `src/llm/knowledge.py` + `knowledge_base/` | 카테고리별 자동 매칭 |
| 5 | HubSpot 이메일 발송 | `src/integrations/senders/__init__.py:send()` → `HubSpotClient.send_email()` | `EMAIL_PROVIDER=hubspot` 일 때 활성 |
| 6 | HubSpot 웹훅 라우트 | `src/api/main.py:90 @app.post("/webhook/hubspot/inbound")` | **라우트는 있음**. 외부 URL + HubSpot 측 구독은 없음 |
| 7 | DB 영속 (Supabase) | `src/db/session.py` | 클라우드 공유 OK |

### 🟡 부분적으로 된 부분

| # | 항목 | 무엇이 빠졌나 |
|---|---|---|
| A | HubSpot 웹훅 수신 | 라우트는 있는데 HubSpot 이 우리 BE 로 호출을 보내려면 **외부에서 접근 가능한 HTTPS URL** 이 필요. 노트북 로컬은 `localhost:8000` 이라 안 닿음 |
| B | "발송 후 상태 갱신" | `HubSpotClient.update_contact()` 메서드는 있지만 발송 플로우에서 **호출 안 함** |
| C | 자동 발송 정책 | `AUTO_SEND_THRESHOLD=1.01` (절대 자동 안 보냄) 가 기본. 사람 승인 거치도록 설계됨 — 사용자 요구는 "받아서 그냥 보내" 라 충돌 |
| D | 메일 받아오는 부분 | 코드는 webhook 이벤트의 `last_message` 필드를 그대로 신뢰. HubSpot 에서 실제 메일 본문을 **풀(fetch)해 오는 로직** 은 없음 |

### ❌ 아예 안 된 부분

| # | 항목 | 비고 |
|---|---|---|
| α | **WhatsApp 발송** | `src/integrations/senders/whatsapp.py` 는 `NotImplementedError` 만 던지는 스텁. Meta Cloud API 미연결 |
| β | **WhatsApp 번호 존재 확인** | "이 번호가 WhatsApp 에 등록되어 있나" 조회 기능 없음 |
| γ | **로컬 cron 폴링** | `src/agents/reply_check.py` 는 답장 감지 용도지 인바운드 신규 폴링이 아님. 새 모듈 필요 |
| δ | **HubSpot 상태 new → meeting link sent** | 발송 후 contact/deal/ticket 의 상태 전환 코드 없음. HubSpot 의 어느 필드를 쓸지(custom property / lifecycle stage / deal stage)도 결정 필요 |
| ε | **단일 실행 파일 배포** | PyInstaller 같은 패키징 안 되어 있음. 현재는 `pip install`, `uvicorn` 명령 필요 |
| ζ | **모니터링 웹 UI** | `/docs` (자동 생성 API 탐색기) 만 있음. 사용자가 쓸 만한 대시보드/메시지 검토 화면 없음 |
| η | **claude CLI 재로그인 안내** | Claude Code 자체의 `/login` 명령에 의존. 별도 자동 로그인 키 메커니즘은 Anthropic 측이 제공해야 가능 (우리가 만들 수 없음) |

---

## C. 격차별 해결안

### C-1. 외부 접근 URL (HubSpot 웹훅 수신)

**문제**: 노트북에서 BE 띄우면 `localhost:8000` 이라 HubSpot 이 못 닿음.

**해결**: `cloudflared` 무료 터널 사용. 한 줄 명령으로 영구 HTTPS URL 생성.

```powershell
# 1) 한 번만 cloudflared 설치 (Windows)
winget install --id Cloudflare.cloudflared

# 2) 임시 터널 (재시작할 때마다 URL 바뀜)
cloudflared tunnel --url http://localhost:8000

# 또는 영구 터널 (Cloudflare 계정 무료, URL 고정)
cloudflared tunnel login                              # 브라우저에서 Cloudflare 로그인
cloudflared tunnel create sales-automation
cloudflared tunnel route dns sales-automation sales.본인도메인.com
cloudflared tunnel --config <path> run sales-automation
```

산출물: `https://sales.본인도메인.com/webhook/hubspot/inbound` 같은 공개 URL.

작업량: 10분 (설치) + 사용자 계정 가입 (Cloudflare 무료).

### C-2. HubSpot 측 웹훅 구독

**문제**: 우리 BE 가 받을 준비됐어도 HubSpot 이 "이쪽으로 쏴" 하는 설정이 없음.

**해결**: HubSpot Private App → Webhooks 탭에서 구독 설정.

```
Target URL: https://sales.본인도메인.com/webhook/hubspot/inbound
Event types:
  - contact.creation             (새 contact 생성)
  - contact.propertyChange (filter: lifecyclestage)  (lead 상태 변경)
```

**+** HubSpot 이 보내는 페이로드는 `subscriptionType`, `objectId`, `propertyName`, `propertyValue` 형태. 현재 우리 라우트의 `InboundWebhookBody` 스키마(`event_type`, `object_id`, `occurred_at`)와 다름. **라우트의 페이로드 매핑 코드 수정 필요**.

작업량: 라우트 수정 30분 + HubSpot 측 설정 10분.

### C-3. 로컬 cron 10분 폴링 (fallback)

**문제**: 터널이 끊기거나 사용 안 하고 싶을 때를 위한 백업.

**해결**: 백엔드 안에 백그라운드 스케줄러 추가 (외부 cron 의존 안 함).

```python
# src/agents/inbound_poller.py (신규)
import asyncio
from datetime import datetime, timedelta, timezone
from ..integrations.hubspot import HubSpotClient
from ..db.session import SessionLocal
from ..db.models import Event
from .inbound import InboundAgent

async def poll_new_inbounds():
    """10분마다 HubSpot 에서 새 contact 들 가져와서 InboundAgent 로 처리.
    
    중복 방지: events 테이블의 last poll timestamp 보다 새로 생긴 contact 만.
    """
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Poll iteration failed")
        await asyncio.sleep(600)

# main.py 에서 startup 이벤트로 task 띄우기:
@app.on_event("startup")
async def start_poller():
    if settings.INBOUND_POLL_ENABLED:
        asyncio.create_task(poll_new_inbounds())
```

`HubSpotClient` 에 `list_contacts_since(timestamp)` 메서드 추가 필요.

작업량: 약 2시간 (구현 + 테스트).

### C-4. HubSpot 에서 메일 본문 실제로 풀(fetch)

**문제**: 웹훅이 알려주는 건 "이런 이벤트 발생함" 뿐. 메일 본문은 별도 호출로 가져와야 함.

**해결**: 이미 `HubSpotClient.get_recent_emails_sync()` 가 있음. `inbound.py:_fetch_contact()` 에서 호출함. 그런데 폼 제출 같은 경우는 메일이 아니라 **submission body** 이므로 별도 처리 필요:
- `event_type=form.submission` → HubSpot Forms API 호출
- `event_type=email.received` → 이미 구현된 engagement fetch
- `event_type=contact.creation` (속성 변경) → contact 의 notes/properties 확인

작업량: 약 1.5시간.

### C-5. 자동 발송 정책 (사람 승인 생략)

**문제**: 현재 `AUTO_SEND_THRESHOLD=1.01` 이라 모든 메시지가 사람 승인 대기.

**해결 옵션 두 가지** (사용자 선택 필요):

**옵션 A** — 무조건 자동발송 (가장 단순)
```env
AUTO_SEND_THRESHOLD=0.0   # 점수 0 이상이면 자동발송 → 모두 자동
```
+ `inbound.py:handle()` 끝에 점수 임계 넘으면 즉시 `senders.send()` 호출하는 분기 추가.

**옵션 B** — 신뢰도 기반 (안전)
```env
AUTO_SEND_THRESHOLD=70    # 점수 70 이상만 자동, 미만은 승인 카드
```
스팸/저점수/이상치는 사람 승인, 고점수는 자동.

요구사항 그대로면 옵션 A. 다만 **첫 1-2주는 옵션 B 권장** (LLM 환각 메시지가 자동 발송되면 사고).

작업량: 30분 (분기 추가 + 환경변수 처리).

### C-6. WhatsApp 번호 존재 확인 + 발송

**문제**: 가장 큰 격차. 두 단계 모두 미구현.

**해결**: **Meta WhatsApp Business Cloud API** 사용.

전제조건:
1. Facebook Business 계정 생성 (무료, 며칠 ~ 1주 검증)
2. WhatsApp Business 계정 만들기
3. 전화번호 인증
4. Access Token 발급
5. 무료 한도: 월 1,000 비즈니스 개시 대화

구현:

```python
# src/integrations/senders/whatsapp.py (재작성)
async def check_whatsapp_exists(phone: str) -> bool:
    """주어진 번호가 WhatsApp 에 등록되어 있는지 확인.
    
    Meta Cloud API 의 contacts endpoint 사용 (deprecated 됐고 현재는 
    실제로는 발송 시도해서 'recipient not on WhatsApp' 에러로 판별하는 게 일반적).
    """
    # 실제로는 발송 시도 → 401/error 라면 미등록으로 처리

async def send_whatsapp_template(phone: str, template_name: str, params: list) -> bool:
    """승인된 template 메시지 발송. 첫 메시지는 반드시 template 이어야 함 (정책)."""
    r = await httpx.post(
        f"https://graph.facebook.com/v18.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "ko"},
                "components": [...]  # 파라미터 채움
            }
        },
    )
```

**주의 사항**:
- WhatsApp 비즈니스 정책상 **첫 메시지는 사전 승인된 template 만** 발송 가능 (사용자가 24시간 안에 응답하면 그 다음 자유 메시지 가능)
- 사용자가 원하는 "이중 채널 발송" 은 **WhatsApp template** 으로 "메일 보냈으니 확인해주세요" 같은 짧은 안내가 현실적
- 한국에서는 WhatsApp 보다 카카오톡 사용률이 압도적이라 — 정말 필요한지 한 번 더 고민 권장

작업량:
- 코드: 약 4시간 (template 등록, API 호출, 에러 처리, 테스트)
- 사업자 검증: 1-2주 대기 (Meta 측)

**대안**: 첫 1-2주는 "전화번호가 있으면 운영자에게 알림만 보내고, 운영자가 수동으로 WhatsApp 연락" 으로 처리. 자동화는 사업자 검증 끝나면 활성화.

### C-7. HubSpot 상태 갱신 (new → meeting link sent)

**문제**: HubSpot 의 어느 필드를 쓸지부터 결정해야 함.

**선택지**:

a) **Lifecycle stage 변경** (lead → opportunity 등)
   - HubSpot 표준 필드. 표준 값 (lead/MQL/SQL/opportunity/customer)
   - "meeting link sent" 같은 사용자 정의 값은 못 넣음

b) **Custom property 사용** (추천)
   - HubSpot Settings → 객체 → Contacts → 속성 → 새 속성 만들기
   - 이름: `inbound_status`, 타입: 드롭다운, 값: `new` / `analyzed` / `meeting_link_sent` / `replied`
   - 코드에서 `update_contact(id, {"inbound_status": "meeting_link_sent"})` 호출

c) **Deal stage** 변경
   - Contact 가 아니라 Deal 이 있어야 함. 인바운드 단계에서 Deal 자동 생성하면 가능

**추천**: b 옵션. 한국어 라벨/값 자유롭게 정의 가능하고 기존 stage 망가뜨리지 않음.

```python
# src/agents/inbound.py 의 handle() 끝에 추가
if msg_sent and self.hubspot:
    await self.hubspot.update_contact(
        contact_id, 
        {"inbound_status": "meeting_link_sent"}
    )
```

작업량: 1시간 (HubSpot 측 속성 생성 30분 + 코드 변경 30분).

### C-8. 단일 실행 파일 배포

**문제**: 현재 `pip install` + `uvicorn` 명령 필요. Spring `.jar` 처럼 더블클릭 하나로 동작했으면 함.

**해결**: **PyInstaller** 로 패키징.

```python
# build.spec (PyInstaller 설정)
a = Analysis(
    ['src/api/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('company_rules/*.md', 'company_rules'),
        ('knowledge_base/*.md', 'knowledge_base'),
        ('src/llm/prompts/**/*.md', 'src/llm/prompts'),
        ('src/db/migrations/*.py', 'src/db/migrations'),
    ],
    hiddenimports=['uvicorn.workers', 'sqlalchemy.dialects.postgresql', ...],
    ...
)
# 빌드
pyinstaller build.spec --onefile --name sales-automation
```

결과:
- Windows: `dist/sales-automation.exe` (~50MB)
- macOS/Linux: `dist/sales-automation` (~50MB)
- 더블클릭 → 콘솔창 뜨고 BE 자동 시작

**제약**:
- claude CLI 는 별도 설치 필요 (PyInstaller 가 외부 CLI 까지 묶지는 못함)
- `.env` 파일은 별도 (사용자 환경별로 다르니)
- 첫 실행 시 압축 해제 때문에 5-10초 느림

작업량: 약 3시간 (spec 작성 + hiddenimports 찾기 + 데이터 경로 핸들링 + 윈도우/맥 빌드 확인).

### C-9. 모니터링 웹 UI

**문제**: 현재 `/docs` (FastAPI 자동 생성 API 탐색기) 만 있음. 사용자용 화면 없음.

**해결**: HTMX + Jinja2 기반 가벼운 대시보드 추가. SPA 대비 의존성 적고 빌드 단계 없음.

라우트 추가 (인증 별도 — 로컬용이라 `localhost` 만 허용):

- `GET /` → 대시보드 (최근 메시지 10건, 카테고리별 카운트, claude CLI 로그인 상태)
- `GET /messages` → 메시지 목록 (필터: status, category, 기간)
- `GET /messages/{id}` → 메시지 상세 (초안 본문, 승인/거절 버튼)
- `POST /messages/{id}/approve` → 승인 + 발송 (기존 `/approve/{id}` 재사용)
- `GET /settings` → 환경변수 상태, 헬스체크 결과, claude CLI 재로그인 안내
- `GET /knowledge` → knowledge_base 파일 목록 + 미리보기 (편집은 file system 직접)

기술 스택:
- Jinja2 templates (이미 의존성에 있음)
- HTMX (CDN 한 줄, JS 빌드 불필요)
- Tailwind via CDN (스타일 빠르게)

작업량: 약 6-8시간 (라우트 6개 + 템플릿 + HTMX 인터랙션 + 인증/CORS).

### C-10. claude CLI 재로그인

**문제**: Anthropic 측 정책상 우리가 "전용 키" 만들어 줄 수 없음. claude CLI 는 OAuth 흐름이라 브라우저 로그인 필수.

**해결**: 사용자 친화적 안내 + 자동 감지.

```python
# src/common/healthcheck.py 강화
def check_claude_login() -> Check:
    r = subprocess.run([CLI, "-p", "ping"], capture_output=True, timeout=5)
    if "Please log in" in r.stderr or r.returncode == 401:
        return Check(
            name="Claude CLI 로그인",
            status="FAIL",
            detail="로그인 만료됨. 터미널에서 `claude /login` 실행해주세요.",
        )
```

대시보드의 `/settings` 페이지에 이 상태를 큼지막하게 표시. 만료되면 빨간 배너 + "재로그인 안내".

작업량: 1시간.

---

## D. 진행 순서 (확정)

전체 약 **26시간** + WhatsApp Meta 검증은 사용자 별도 진행.

### Phase 1 — 받기 자동화 (5시간)
완료 시: HubSpot 새 contact 생기면 자동으로 BE 가 받아서 DB 에 초안 저장.

1. C-1 cloudflared 터널 안내 (10분)
2. C-2 HubSpot 웹훅 구독 + 페이로드 매핑 수정 (40분)
3. C-3 10분 cron 폴링 fallback (2시간)
4. C-4 메일 본문 fetch 강화 (1.5시간)

### Phase 2 — 발송 + 상태 갱신 + WhatsApp (4시간)
완료 시: 사용자가 웹에서 "보내기" 누르면 HubSpot 메일 + WhatsApp 동시 시도 + 상태 `meeting_link_sent` 로 변경.

5. C-7 HubSpot custom property `inbound_status` + 발송 후 자동 갱신 (1.5시간)
6. C-6 WhatsApp Cloud API 풀 구현 + 게이팅 (2시간)
7. 이메일 + WhatsApp 이중 발송 디스패처 (30분)

### Phase 3 — knowledge_base DB 이전 (3시간)
완료 시: knowledge_base 가 DB 에 저장되어 웹 UI 에서 편집 가능. 기존 .md 는 1회 마이그레이션.

8. `knowledge_documents` 테이블 + 마이그레이션 스크립트 (1.5시간)
9. `src/llm/knowledge.py` 의 로더 DB 기반으로 전환 (1시간)
10. 기존 `knowledge_base/*.md` → DB 일괄 import 스크립트 (30분)

### Phase 4 — 웹 UI (10시간)
완료 시: 비개발자가 브라우저에서 모든 운영 가능.

11. 기본 인프라 (Jinja2 + HTMX + Tailwind CDN, 로컬 전용 인증) (1시간)
12. 대시보드 (`GET /`) — 최근 메시지, 카테고리/상태별 카운트 (1.5시간)
13. 메시지 목록 + 상세 (`GET /messages`, `GET /messages/{id}`) (2시간)
14. 메시지 발송 버튼 (`POST /messages/{id}/send`) — 기존 approve 로직 재사용 (1시간)
15. knowledge_base CRUD (`GET/POST/PUT/DELETE /knowledge`) (2.5시간)
16. 설정 페이지 (헬스체크 + claude CLI 로그인 상태) (1시간)
17. 메시지 편집 (LLM 초안 손보기) (1시간)

### Phase 5 — PyInstaller 단일 실행 파일 (4시간)
완료 시: 비개발자가 `sales-automation.exe` 더블클릭 하나로 BE 띄움.

18. `build.spec` 작성 + hidden imports + 데이터 번들 (2시간)
19. 첫 실행 시 `.env` 자동 생성 마법사 (1시간)
20. Windows 빌드 + 안티바이러스 오탐 회피 검증 (1시간)
21. (선택) macOS 빌드 — 사용자 Mac 도 쓰면 추가

**총 26시간** (claude CLI 환경, 본인 노트북 단독 운영 기준)

---

## E. 결정된 정책 (2026-05-15)

| Q | 결정 |
|---|---|
| 자동 발송 정책 | **수동 — 웹 UI에서 "보내기" 버튼 클릭으로 발송**. AUTO_SEND_THRESHOLD=1.01 유지. 검토 단계가 사실상 "예약" 역할. |
| HubSpot 상태 필드 | **Custom property `inbound_status`** 신규 생성 (값: `new` / `analyzed` / `meeting_link_sent` / `replied`) |
| WhatsApp | **풀 구현, key 가드**. WHATSAPP_ENABLED=true + WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID 셋이 있으면 즉시 활성. 글로벌 영업 대상이라 필요. |
| 웹 UI 깊이 | **풀 — knowledge_base 편집까지**. 추가: knowledge_base 자체를 파일에서 DB로 이전 (편집·공유 편의성). |
| 패키징 | **PyInstaller 단일 실행 파일**. 비개발자 대상이라 CLI/pip 거치는 거 부담. |

---

## F. 비용/리스크 요약

| 항목 | 비용 | 리스크 |
|---|---|---|
| cloudflared 무료 터널 | $0 | 무료는 URL 매번 바뀜 (도메인 있으면 영구 URL) |
| HubSpot 웹훅 | $0 | Private App 무료 한도 내 |
| claude CLI | $0 | Anthropic 측 정책 변경 시 영향 가능 |
| Meta WhatsApp | 월 1,000 대화 무료 | 비즈니스 검증 1-2주 + template 사전 승인 필요 |
| Supabase Postgres | $0 (500MB) | 일주일 미사용 시 일시중지 (자동 깨어남) |
| PyInstaller | $0 | 안티바이러스 오탐 가끔 발생 |
| 자동 발송 사고 | — | LLM 환각으로 잘못된 정보 발송 가능. **첫 2주는 옵션 C(수동) 강력 권장** |

---

## G. 시작 순서

위 5개 Phase 를 순서대로 진행하는 게 안전. 사이클이 짧으니 Phase 1 끝낼 때마다 한 번 검토 받고 다음으로.

Phase 1 부터 시작.
