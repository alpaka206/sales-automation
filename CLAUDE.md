# Project Context

PERSO Inbound is a FastAPI workflow for inbound inquiry handling and customer operations.

## Invariants

- **Pre-launch safety (대전제, top priority).** `LIVE_EXTERNAL_WRITES` defaults to `false` (SAFE). While safe: every HubSpot write is hard-blocked (`guard_external_write` → `ExternalWriteBlocked`), Google Sheets writes are disabled (`writes_enabled()`), and every outbound email is force-routed to `ronald@estsoft.com` (`resolve_send_override`) — a customer can never be emailed even if `SEND_OVERRIDE_EMAIL` is cleared. Reads stay on. Enforced in `src/common/safe_mode.py`; guaranteed behavior is pinned by `tests/test_safe_mode.py`. Any NEW external-write/send path MUST call `guard_external_write` (or route through `resolve_send_override`) and add a line to `tests/test_safe_mode.py`.
- **Email is a separate axis, and it is the one thing still held back.** As of 2026-08-04 the operator's posture is "메일 발송되는 것만 막고 나머지는 모두 다": HubSpot and the sales workbook write for real, and **nothing is emailed at all**. Two module constants in `safe_mode.py`, deliberately not env: `EMAIL_SENDING_ENABLED = False` (the no-send switch, at the lowest chokepoint so it also catches callers that bypass `senders.send()`) and `FORCE_TEST_RECIPIENT = True`, kept on underneath it so that flipping sending back on resumes delivery pinned to one address rather than reaching customers — two mistakes required, not one. Going live on email = `EMAIL_SENDING_ENABLED = True` **and** `FORCE_TEST_RECIPIENT = False` **and** clear `SEND_OVERRIDE_EMAIL`. `tests/conftest.py` turns both off for the suite so the sender tests still cover real delivery; the shipped values are asserted from source in `tests/test_safe_mode.py`.
- **The CRM/workbook are LIVE.** `LIVE_EXTERNAL_WRITES=true` with `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` both on, so a stage moved in the console moves the HubSpot ticket and updates the Inbound DB row. Every screen write goes through the same routes the Jinja forms used, which is why that stayed true through the React port.
- **Per-destination switches are subordinate.** `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` (both default `true`) select which destinations go live *after* the master is on; neither can permit a write while `LIVE_EXTERNAL_WRITES` is `false`. `guard_external_write("<channel>:<action>")` picks the gate from the label prefix, and an unregistered channel falls back to the master — so a new write path is blocked by default.
- HubSpot tickets are accepted only from `HUBSPOT_TICKET_STAGE_NEW` when a ticket ID exists.
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
