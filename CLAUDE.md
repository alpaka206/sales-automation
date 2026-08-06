# Project Context

PERSO Inbound is a FastAPI workflow for inbound inquiry handling and customer operations.

## Invariants

- **Pre-launch safety (대전제, top priority).** `LIVE_EXTERNAL_WRITES` defaults to `false` (SAFE). While safe: every HubSpot write is hard-blocked (`guard_external_write` → `ExternalWriteBlocked`), Google Sheets writes are disabled (`writes_enabled()`), and every outbound email is force-routed to `ronald@estsoft.com` (`resolve_send_override`) — a customer can never be emailed even if `SEND_OVERRIDE_EMAIL` is cleared. Reads stay on. Enforced in `src/common/safe_mode.py`; guaranteed behavior is pinned by `tests/test_safe_mode.py`. Any NEW external-write/send path MUST call `guard_external_write` (or route through `resolve_send_override`) and add a line to `tests/test_safe_mode.py`.
- **Email is a separate axis, and it is the one thing still held back.** As of 2026-08-04 the operator's posture is "메일 발송되는 것만 막고 나머지는 모두 다": HubSpot and the sales workbook write for real, and **nothing is emailed at all**. Two module constants in `safe_mode.py`, deliberately not env: `EMAIL_SENDING_ENABLED = False` (the no-send switch, at the lowest chokepoint so it also catches callers that bypass `senders.send()`) and `FORCE_TEST_RECIPIENT = True`, kept on underneath it so that flipping sending back on resumes delivery pinned to one address rather than reaching customers — two mistakes required, not one. Going live on email = `EMAIL_SENDING_ENABLED = True` **and** `FORCE_TEST_RECIPIENT = False` **and** clear `SEND_OVERRIDE_EMAIL`. `tests/conftest.py` turns both off for the suite so the sender tests still cover real delivery; the shipped values are asserted from source in `tests/test_safe_mode.py`.
- **The CRM/workbook are LIVE.** `LIVE_EXTERNAL_WRITES=true` with `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` both on, so a stage moved in the console moves the HubSpot ticket and updates the Inbound DB row. Every screen write goes through the same routes the Jinja forms used, which is why that stayed true through the React port.
- **Per-destination switches are subordinate.** `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` (both default `true`) select which destinations go live *after* the master is on; neither can permit a write while `LIVE_EXTERNAL_WRITES` is `false`. `guard_external_write("<channel>:<action>")` picks the gate from the label prefix, and an unregistered channel falls back to the master — so a new write path is blocked by default.
- HubSpot tickets are accepted only from `HUBSPOT_TICKET_STAGE_NEW` when a ticket ID exists.
- **서명은 사람이 고르는 것이고, 코드는 본문에 넣지 않는다.** 예전에는 두 벌이었다 — 회사 규칙 안의 `{{__signature__}}` 가 `signature_ko` 행을 프롬프트에 끼워 모델이 **본문에** 서명을 쓰게 했고, 그래서 발송 경로에는 운영자가 다른 서명을 고르면 그 텍스트를 도로 찾아 떼어내는 기계(`strip_known_signature`, 번역된 메일에서는 메일 주소를 앵커로 잘랐다)까지 있었다. 지금은 한 벌이다: 운영자가 초안에서 고르고 `발송` 을 누르면 그때 본문 아래로 붙는다(`messages.signature_key` → `branded_signature_html` → `to_html_email`). 되살리기 전에 왜 두 벌이면 안 되는지부터 읽어라 — 0061 의 docstring 에 적혀 있다.
  - **서명은 데이터다.** 접두사 `signature_` **하나**가 목록의 서명 묶음 · 검토 화면 고르개 · 삭제 가능 여부를 동시에 가른다. 셋이 같은 집합을 가리켜야 한다 — 예전에 `signature_html_`(고르개)과 `signature_`(목록)로 갈라져 있었고, 그래서 화면에는 서명으로 보이는데 고를 수도 지울 수도 없는 행이 있었다. 추가·수정·삭제 전부 콘솔에서 되고, 코드가 이름으로 찾는 서명은 하나도 없다. 글로 써도 되고 HTML 로 써도 된다.
  - **서명에 언어는 없다** (0063). 어떤 코드도 언어로 서명을 고르지 않는다 — 고르는 것은 사람이다 — 그래서 묻지도, 보여주지도, 저장하지도 않는다. `email_templates.language` 열 자체는 남는다: `auto_ack` / `auto_ack_en` 은 정말로 한 메일의 두 언어다.
  - **접수확인 아래에는 서명이 아니라 로고가 붙는다** — `auto_ack_footer` 행 (0062). 붙는 통로는 서명과 같은 `signature_key` 지만 키가 접두사 **밖에** 있어서 검토 화면 고르개에는 안 나온다. 아직 아무도 읽지 않은 메일에 담당자 서명을 붙이면 그 사람이 쓴 메일로 읽히는데, 정작 답은 며칠 뒤 다른 사람이 쓸 수도 있다. 서명은 사람이 검토하고 누르는 **첫 답변**부터다.
- **수주 고객은 Client ID 로 묶인다.** `clients` 아래 `client_contracts`(차수별), 그 아래 크레딧 지급 회차·분납 회차·클레임. 소통 히스토리만 예외로 고객 단위라 새 테이블을 만들지 않고 `customer_interactions` 에 `contract_seq` 만 붙였다 — 협상 단계 대화가 계약보다 먼저 쌓이고 그대로 이어져야 한다(0065).
  - **고객을 `contacts` 로 대신할 수 없다.** Contact 는 이메일 신원이라 한 회사에 담당자가 셋이면 셋이고, Outbound·Interactive·AX 고객은 문의를 보낸 적이 없어 아예 없다. 인바운드 고객만 `clients.contact_id` 로 연결된다.
  - **Client ID 는 고객사 하나에 하나.** 전에는 문의 하나에 하나였다(`suggest_inbound_client_id` 가 대화마다 새 번호). 그러면 같은 회사가 두 번 문의할 때 계약·크레딧·소통 히스토리가 두 갈래로 갈라진다. `agents/client_ids.py` 가 ① 그 연락처의 번호 ② 같은 회사 도메인의 다른 담당자 번호(**개인 메일 도메인 제외** — gmail 둘을 한 회사로 묶으면 남의 계약이 보인다) ③ 새 발급 순으로 정한다.
  - **고객 종류는 저장하지 않는다.** 번호대가 곧 종류다(1000 Inbound / 2000 GTM Outbound / 3000 Interactive / 4000 AX / 9000 레거시). 둘 다 저장하면 서로 다른 행이 반드시 생긴다.
  - **Won 감지는 `stage_sync.sync_stage_from_hubspot` 한 곳.** 웹훅·10분 폴러·수동 최신화가 전부 이 함수를 지난다. 감지를 세 군데 두면 하나가 조용히 빠진다. Won 티켓은 `pending_won` 에 쌓이고 계약 정보는 사람이 채운다 — 금액도 기간도 없는 고객이 활성 고객 수와 예상 MRR 을 오염시키면 안 된다.
  - **환율은 쓴 시점의 값을 행에 박는다.** 크레딧(`공급가 ÷ 분당 단가 × 60`)에 쓴 환율은 계약에, 입금액에 쓴 환율은 결제 회차에. 오늘 환율로 과거를 다시 환산하면 지난달 매출이 이번 달에 바뀐다. 예상 MRR 카드는 **오늘 고시가를 가져와** 쓴다(인증 불필요, ECB. 수출입은행 키가 있으면 그쪽이 우선). 손으로 적는 칸이었는데, 그러면 두 사람이 다른 환율로 다른 MRR 을 보고 그 값이 언제 것인지 아무도 모른다. `MRR_FX_RATE` 는 조회가 실패했을 때의 바닥값이다. **한국에서 낮에 보면 거의 항상 전일자 고시가 나온다 — 정상이다**: ECB 는 유럽 오후에 하루 한 번 낸다(KST 밤). 그래서 화면은 "오늘" 이라 쓰지 않고 실제 고시일을 적는다.
  - **화면은 운영자가 준 HTML 목업 그대로**다. `static/won.css` 는 그 목업의 스타일을 전부 `.won` 아래로 스코프해 옮긴 것이고, `:root` 변수도 `.won` 에 건다 — `:root` 에 두면 콘솔 전체 색이 바뀐다. 파일 끝의 되돌림 블록은 **목업이 값을 주지 않아 console.css 가 흘러 들어온 속성**만 다룬다.
- **자동 회신은 대화당 한 번뿐이다.** 첫 문의에 접수확인이 나가고 초안이 하나 만들어진다. 그 대화에 이미 회신(접수확인 제외)이 있으면, 이후 고객 메시지는 **기록만 되고 초안을 만들지 않는다** — 이후 회신은 사람이 직접 등록한다. `_persist_placeholder` 가 `reply_message_id=None` 을 돌려주고 `handle()` 이 `skipped_reply_exists` 로 끝낸다. 조건이 "두 번째 inbound" 가 아니라 "이미 회신이 있다" 인 이유: 티켓 하나에 이벤트가 여러 번 온다(웹훅 + 10분 폴러 + 티켓 변경). `tests/test_inbound_flow.py` 가 고정한다.
- **답변의 형식·톤 규칙은 콘솔에 한 벌만 둔다.** `policy_sources(mode='rules')` 의 「공통 원칙 및 가드레일」이 그 한 벌이고, `draft_reply.md` 는 그것을 따르라고 가리키기만 한다. 양쪽에 적으면 운영자가 콘솔에서 고친 쪽과 배포해야 바뀌는 파일이 조용히 어긋난다. `tests/test_reply_style.py::test_the_layout_rules_live_in_exactly_one_place` 가 고정한다.
  - **가격은 문서와 코드가 같은 말을 해야 한다.** 문서의 가드레일이 "구체적 가격 숫자를 쓰지 않는다" 이므로 `_PRICING_RULE_NORMAL` 도 그렇게 말한다. 예전에는 정반대였고(코드는 "금액을 명시하라"), 그때 이기는 쪽은 코드였다. `enforce_first_reply_no_price` 는 첫 회신에만 도는 하드 가드로 남는다 — 모든 회신에 걸면 운영자가 일부러 적은 금액을 조용히 지운다.
  - **어떤 문서를 쓸지는 모델이 고른다.** 매핑을 코드에 박으면 문서 이름이 바뀌거나 지워질 때마다 흔적 없이 끊긴다. 모델이 보는 것은 본문이 아니라 인덱스 한 줄(`slug·title·categories·tags·summary`)이고, `summary` 는 정책 문서의 **「언제 쓰는가」 칸**(0064)이다 — 비면 본문 앞 400자. 사본의 `categories` 는 `["all"]` 이어야 한다: 라우터가 실패해 유형 매칭으로 떨어질 때 후보가 0개가 되면 **문서 없이** 답을 쓴다.
- The first receipt acknowledgement may send automatically; the detailed reply always requires human approval. This is now structural, not configured: the score-vs-`AUTO_SEND_THRESHOLD` branch that could set `approved` on its own is gone, along with the setting, so `_finalize_draft` always writes `pending_approval`. Pinned by `tests/test_safe_mode.py` and `tests/test_inbound_auto_ack.py`.
- **The inquiry category is stored and shown; which document answers it is NOT.** `Conversation.inquiry_category` (0049) is what the 회신 및 검토 list shows where 채널 used to be — channel was `email` on every row. `support` / `spam` / `recruiting` render as **UnQualified**, which means "not a sales lead", not "do not reply": those still get an answer, from the CS guide or the intro document. It also replaced the 검토 필요 flag (0047, dropped in 0049) — "CS 문의" says which one to open first far better than "확인이 필요합니다" did. `Conversation.inquiry_subject` (renamed from `topic` in 0041) still holds the customer's own subject line.
  - **The category→document mapping is deliberately not in code.** The model reads the document index (title · summary · tags) and picks; the category and the inquiry language are hints in the prompt, not a lookup table. Policy changes and Notion titles change — a mapping frozen in Python breaks on both, with nothing on screen to show it broke. `spam` no longer short-circuits to "no documents" for the same reason.
- SMTP performs real delivery. HubSpot's CRM email object is used only to log a successful delivery.
- Slack approval notifications are emitted only after a detailed draft is ready.
- `Message.direction` uses `inbound` for received messages and `outgoing` for our replies.
- Personal email domains are never grouped as one company.
- Existing conversation progress rows are append-only.

- **정책·지식 문서의 원본은 이 콘솔이다. 노션에서 받아오는 코드는 하나도 없다.**
  `이메일 템플릿 → 정책 문서 → 직접 추가`(제목+본문), 어떤 문서든 `수정` 가능. 다섯 가지를
  시도했고 전부 막혔다 — 통합 토큰 발급 불가, 쿠키는 `file.notion.com`에서 403, 로컬
  스크립트는 사내망이 5432/6543을 막아 DB에 못 닿고, URL만 등록하면 본문이 영원히 비고,
  **Export zip은 부모 페이지 하나만 실어 온다**(정책 페이지가 가리키는 문서 여덟 개는
  자식이 아니라 링크라서 `Include subpages`로도 안 따라온다 — 2026-08-05 실측).
  그래서 `notion.py` · `notion_export.py` · `notion_session.py` · `policy_push.py` ·
  `api/policy_api.py` · `scripts/sync_notion_local.py` · `sync_policy_sources` · `NOTION_*`
  설정을 전부 지웠고, `policy_sources`에서 `notion_url`/`last_synced_at`/`last_error`도
  뺐다(0050). 답할 수 없는 질문을 스키마에 남기면 다음 사람이 답을 찾으러 간다.
  **되살리기 전에 `docs/정책문서-동기화-설계.md` §5를 돌려라** — 조건이 바뀌었으면 되돌리는
  게 맞고, 안 바뀌었으면 그 문서에 왜 안 되는지가 실측과 함께 적혀 있다.
  - 본문 없는 등록은 만들 수 없다 — 문서를 만드는 화면이 본문을 같이 받는다. URL만 등록해
    두던 폼이 `body`가 영원히 빈 행을 만들었고, zip도 결과적으로 같은 일을 했다.
  - 콘솔 편집은 `refresh_knowledge_copy`로 라우터가 읽는 사본까지 즉시 간다. 안 그러면
    화면엔 새 내용, 회신은 옛 내용이 되고 눈치챌 방법이 없다.

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, React (Vite + TypeScript + React Query)
- Gemini on Vertex AI (`flash` for routing/classification, `pro` for customer replies)
- SQLite locally; PostgreSQL-compatible migrations
- SMTP delivery, HubSpot CRM synchronization, optional Slack

## Data flow

`HubSpot webhook / 10-minute poll → immediate acknowledgement → Gemini + policy docs → review queue + Slack → SMTP → HubSpot timeline + ticket stage`

Customer operations reuse the same Contact and Conversation records. `CustomerProfile`, `CustomerInteraction`, and `ContractRecord` add manual pipeline fields, cross-channel history, contracts, payments, and renewal insights without duplicating the inbound pipeline.

## Development

```powershell
.\.venv\Scripts\python.exe -m src.db.migrate
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Do not send live messages in tests. `tests/conftest.py` disables background integrations regardless of the developer `.env`.

## Front end

The console is React (`frontend/`, Vite + TypeScript + React Query), served by FastAPI at
`/app`; every old page URL 302s there. There are no HTML templates left and no template
engine — sign-in was the last holdout, and it serves the same SPA document (`/auth/*` is
exempt from the auth gate, so React can draw it before a session exists).

- **Build before packaging.** `npm --prefix frontend ci && npm --prefix frontend run build`
  writes `src/api/static/app/`, which is gitignored and shipped via
  `[tool.setuptools.package-data]`. Skip it and `/app` answers 503. The Dockerfile's node
  stage does this itself, so `docker build` needs no prior step.
- **`npm --prefix frontend test`** replays 1,512 quotes the pre-React calculator
  rendered against `src/lib/quote.ts`. `frontend/test/quote.golden.json` is not a fixture
  to refresh: a failure means the console now quotes a different price than the
  calculator the sales team has been using.
- **Styling is `static/console.css`**, linked rather than bundled — one copy of the design
  for the SPA and for the sign-in pages. There is no CSS framework.
- **Reads go through `/api/ui/*`**, which calls the SAME context builders the templates
  used, so a screen's data has one definition. **Writes go to the existing routes** — the
  send guard, stage sync and safe-mode block stay in one place.
- **`/api/ui/events` is SSE.** Writes publish a topic; every open console invalidates its
  cache. This is what makes a change visible in another tab or to another operator, and
  React state alone cannot do it. In-process fan-out: multi-worker needs Redis pub/sub.
