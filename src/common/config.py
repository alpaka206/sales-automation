"""
Settings — loads from `.env` via pydantic-settings.

Every value has a default so importing this module never crashes,
even before the operator fills in `.env`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # ----- LLM (Gemini on Vertex AI — the only provider) -----
    # Hybrid model tiers:
    #   GEMINI_MODEL      → fast/cheap "flash" tier for light judgment
    #                       (classification, scoring, doc routing, enrichment).
    #   GEMINI_MODEL_PRO  → high-quality "pro" tier for customer-facing drafting
    #                       (inbound reply drafts).
    # Code picks the tier per call via LLMClient.complete(..., tier="flash"|"pro").
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_MODEL_PRO: str = "gemini-2.5-pro"
    # Service-account JSON (full contents as a string). No API key is used.
    GOOGLE_CREDENTIALS_JSON: str = ""
    # Project falls back to the JSON's project_id when left empty.
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "global"

    @property
    def gemini_model_for(self) -> dict[str, str]:
        """Map of tier name → resolved model id. Use settings.gemini_model_for[tier]."""
        return {"flash": self.GEMINI_MODEL, "pro": self.GEMINI_MODEL_PRO}

    # ----- HubSpot -----
    # Private App access token (pat-na1-...). Accepts either env name:
    # HUBSPOT_ACCESS_TOKEN (new naming) or HUBSPOT_PRIVATE_APP_TOKEN (legacy).
    HUBSPOT_PRIVATE_APP_TOKEN: str = Field(
        default="",
        validation_alias=AliasChoices("HUBSPOT_ACCESS_TOKEN", "HUBSPOT_PRIVATE_APP_TOKEN"),
    )
    HUBSPOT_OWNER_ID: str = ""
    HUBSPOT_WEBHOOK_SECRET: str = ""
    # ----- [B2B] AI Dubbing ticket pipeline stage ids -----
    # The env names below mirror the stage labels in HubSpot (New / Qualified /
    # Negotiating / Reminder Sent / Won / Lost / Concluded). Stages get
    # RENAMED in HubSpot without changing their id — "Meeting link sent" became
    # "Qualified", and "Closed" became "Not a Fit" then "Concluded" — so every former
    # spelling stays as an
    # alias: an existing .env or Render dashboard keeps working, and the id behind it is
    # the same row either way. Same pattern as HUBSPOT_ACCESS_TOKEN above. Find an id in
    # HubSpot Settings → Objects → Tickets → Pipelines (click a stage → copy id), or run
    # scripts/list_ticket_stages.py.
    #
    # Only tickets in this stage are treated as new inbound inquiries. Empty keeps the
    # existing "all newly-created tickets" behavior.
    HUBSPOT_TICKET_STAGE_NEW: str = ""
    # After SMTP send completes, move the linked ticket here. Empty = don't touch stage.
    HUBSPOT_TICKET_STAGE_AFTER_SEND: str = Field(
        default="",
        validation_alias=AliasChoices(
            "HUBSPOT_TICKET_STAGE_QUALIFIED",
            "HUBSPOT_TICKET_STAGE_MEETING_LINK_SENT",
            "HUBSPOT_TICKET_STAGE_AFTER_SEND",
        ),
    )
    HUBSPOT_TICKET_STAGE_NEGOTIATION: str = Field(
        default="",
        validation_alias=AliasChoices(
            "HUBSPOT_TICKET_STAGE_NEGOTIATING", "HUBSPOT_TICKET_STAGE_NEGOTIATION"
        ),
    )
    HUBSPOT_TICKET_STAGE_CLOSED_LOST: str = Field(
        default="",
        validation_alias=AliasChoices(
            "HUBSPOT_TICKET_STAGE_LOST", "HUBSPOT_TICKET_STAGE_CLOSED_LOST"
        ),
    )
    # Stages that exist in the real pipeline. Declared so the values are actually read
    # (pydantic's extra="ignore" silently drops anything undeclared).
    HUBSPOT_TICKET_STAGE_REMINDER_SENT: str = ""
    HUBSPOT_TICKET_STAGE_WON: str = ""
    # 지금 이름은 "Concluded" 입니다(2026-08-19). 그 전엔 "Not a Fit", 그 전엔 "Closed",
    # 그 전엔 "Unqualified" — 전부 alias 로 남습니다. **id 는 한 번도 안 바뀌었습니다**
    # (1404814097). 이름을 따라 키를 바꾸면 대화·프로필 두 열을 옮기는 마이그레이션이
    # 필요하고, 다음에 이름이 또 바뀌면 그걸 또 합니다.
    HUBSPOT_TICKET_STAGE_CLOSED: str = Field(
        default="",
        validation_alias=AliasChoices(
            "HUBSPOT_TICKET_STAGE_CONCLUDED",
            "HUBSPOT_TICKET_STAGE_NOT_A_FIT",
            "HUBSPOT_TICKET_STAGE_CLOSED",
            "HUBSPOT_TICKET_STAGE_UNQUALIFIED",
        ),
    )
    # NO_RESPONSE 는 지웠습니다 (2026-08-19). 이름이 바뀐 것이 아니라 **단계가 없어졌으므로**
    # alias 로 남기지 않습니다 — 남겨 두면 없는 단계의 id 를 계속 읽으려 듭니다. 그 값을 쓰던
    # 행은 이관 0076 이 Concluded 로 접었습니다.
    # These seven are the whole pipeline. FOLLOW_UP_NEEDED / CONTRACTED / ONBOARDING /
    # ACTIVE were retired in migration 0040, NO_RESPONSE in 0076 — do not redeclare them
    # without a matching HubSpot stage and an entry in stage_sync.LOCAL_STAGE_TO_SETTING.
    # Set to true ONLY if the HubSpot account has a custom `inbound_status`
    # text property on contacts. We write "analyzed" / "meeting_link_sent" to
    # it for operator visibility, but the value is never read back, and the
    # ticket pipeline_stage covers the same role in the new workflow. Default
    # off — keeping it on without the property logs a 400 every webhook.
    HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS: bool = False

    # ----- Email -----
    # SMTP delivers mail; HubSpot's email API only receives a timeline copy.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Sales Team"
    SMTP_FROM_EMAIL: str = ""

    # ----- Launch safety switch (pre-launch "대전제") -----
    # Master kill switch for ALL external side effects. Default False = SAFE:
    #   - every HubSpot write is hard-blocked (ticket stage, contact/inbound status,
    #     timeline email) → the real HubSpot account cannot change during testing;
    #   - Google Sheets writes are disabled (no test rows in the shared workbook);
    #   - every outbound email is force-routed to the test recipient (ronald@…),
    #     so no customer is ever emailed even if SEND_OVERRIDE_EMAIL is cleared.
    # Reads (HubSpot GET, Gemini, homepage fetch) stay on. Going live = set this to
    # true AND clear SEND_OVERRIDE_EMAIL. See src/common/safe_mode.py.
    LIVE_EXTERNAL_WRITES: bool = False
    # Per-destination switches, consulted ONLY once LIVE_EXTERNAL_WRITES is true.
    # Default true, so flipping the master alone behaves exactly as before. Set one to
    # false to go live on one destination and not the other — e.g. keep mirroring into
    # the workbook while HubSpot is mid-reorganisation and a stray stage move would
    # fight whoever is editing the pipeline. Neither can override the master.
    LIVE_HUBSPOT_WRITES: bool = True
    LIVE_SHEETS_WRITES: bool = True

    # ----- Test mode: redirect ALL real sends to one address -----
    # When non-empty, every customer-facing inbound reply is rerouted to this
    # address and sent via SMTP — the HubSpot provider is bypassed so nothing lands
    # on a real contact's timeline. The original intended recipient is preserved in
    # the subject as "[TEST→original]".
    # Requires SMTP_USERNAME/SMTP_PASSWORD. Leave EMPTY in production.
    SEND_OVERRIDE_EMAIL: str = ""

    # ----- 수주 고객 -----
    # 한국수출입은행 OpenAPI 인증키. 있으면 결제 등록·입금 완료 시 그 날짜의 매매기준율을
    # 자동으로 채웁니다. 없으면 운영자가 직접 넣고, 값은 어느 쪽이든 결제 행에 남습니다 —
    # 조회 실패가 저장을 막으면 안 됩니다.
    KOREAEXIM_API_KEY: str = ""
    # 예상 MRR 카드가 USD 계약을 원화로 환산할 때 쓰는 환율. **한 사람이 바꾸면 모두가 같은
    # 숫자를 봅니다** — 화면마다 다른 환율이면 두 사람이 다른 MRR 을 보고 회의에 들어옵니다.
    # 과거 입금액은 이 값을 쓰지 않습니다(결제 행에 그날 환율이 박혀 있습니다).
    MRR_FX_RATE: float = 1380.0

    # ----- Approval -----
    SLACK_ENABLED: bool = False
    APPROVAL_CHANNEL: Literal["slack", "none"] = "none"
    SLACK_BOT_TOKEN: str = ""
    SLACK_APPROVAL_CHANNEL_ID: str = ""

    # ----- Reports -----
    REPORT_EMAIL_TO: str = ""

    # ----- Inbound poller -----
    INBOUND_POLL_ENABLED: bool = False
    INBOUND_POLL_INTERVAL_SECONDS: int = 600
    INBOUND_INITIAL_LOOKBACK_HOURS: int = 24
    # Webhook and poller only enqueue; this worker performs HubSpot/AI work with retries.
    INBOUND_WORKER_ENABLED: bool = True

    # ----- Inbound auto-acknowledgement -----
    # On the FIRST inbound of a thread, immediately send a "we received your
    # message, we'll send a detailed reply shortly" acknowledgement — WITHOUT human approval,
    # in the inquiry's language (enforced in code). It does not change the
    # ticket/draft status. Editable text lives in the ``auto_ack`` email template.
    INBOUND_AUTO_ACK_ENABLED: bool = True

    # ----- Send worker -----
    SEND_WORKER_ENABLED: bool = False
    SEND_RATE_PER_MINUTE: int = 5
    # Safety cap on customer emails/day. It guards the Gmail/SMTP sender quota
    # (free Gmail ~500/day; exceeding throttles the account) and sender reputation.
    # Set generously rather than disabling (0 = unlimited). 400 ≈ free-Gmail margin.
    DAILY_SEND_LIMIT: int = 400
    SEND_JITTER_SECONDS: int = 15

    # AUTO_SEND_THRESHOLD used to live here. Score-based auto-approval is gone:
    # a detailed reply now always waits for a human, so there is no threshold to
    # set and no setting that could re-enable unattended sending.

    # ----- DB -----
    DATABASE_URL: str = "sqlite:///./data/app.db"

    # ----- App -----
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    WEB_CONCURRENCY: int = 1
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Seoul"

    # ----- Google Sheets (inbound mirror) -----
    # This workbook is the fixed sales-team format. Sync is user-OAuth-only —
    # Workspace policy blocks sharing the file with a service account.
    # The OAuth client is GOOGLE_OAUTH_CLIENT_ID/_SECRET below; there is no separate
    # Sheets client. Setup is one scope added to that existing client.
    # Dedicated encryption key for delegated refresh tokens. Do not reuse the
    # browser session or internal API signing secret in production.
    # Only needed for the browser "Connect" flow — a refresh token supplied below
    # is never written to the database, so it needs no encryption key.
    GOOGLE_TOKEN_ENCRYPTION_KEY: str = ""
    # Refresh token for the workbook owner's own Google account, issued once by
    # scripts/connect_google_sheets.py. When set it REPLACES the /pipeline connect
    # button: the app authenticates straight from env, so a fresh deploy or a reset
    # database needs no click. Treat it like a password.
    GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN: str = ""
    # 노션 설정은 전부 없어졌습니다. 정책·지식 문서는 콘솔에 직접 붙여넣습니다 —
    # 자동으로 받아 올 방법이 없어서이고, 무엇을 시도했는지는
    # docs/정책문서-동기화-설계.md 에 있습니다.

    # Display only — which account that refresh token belongs to, shown on /pipeline.
    GOOGLE_SHEETS_ACCOUNT_EMAIL: str = ""
    # The live workbook, "사본 [Perso AI] B2B 통합 대시보드". This default is what a
    # deployment uses when the env var is unset, so it must name the CURRENT workbook —
    # leaving the previous one here would send writes to a stale sheet the moment
    # LIVE_SHEETS_WRITES is on.
    GOOGLE_SHEETS_SPREADSHEET_ID: str = "1NWdn-rH3BdfRPCldglDnQnAl4IFmP9LnRkDkGBdmGRo"
    GOOGLE_SHEETS_INBOUND_TAB: str = "Inbound DB"
    GOOGLE_SHEETS_ORDERS_TAB: str = "수주 DB"

    # ----- Domain enrichment -----
    INBOUND_DOMAIN_ENRICHMENT_ENABLED: bool = True
    INBOUND_DOMAIN_HOMEPAGE_FETCH: bool = True
    INBOUND_DOMAIN_FETCH_TIMEOUT_SECONDS: float = 8.0
    # Fall back to Gemini's Google Search grounding so well-known companies resolve
    # even when their homepage blocks bots / is parked / times out.
    INBOUND_DOMAIN_SEARCH_GROUNDING: bool = True
    INBOUND_DOMAIN_REANALYZE_DAYS: int = 90

    # 빈 문자열이면 auth 미들웨어가 모든 요청을 거부합니다(보안). 운영 전 반드시 강한 토큰 설정.
    INTERNAL_API_TOKEN: str = Field(default="")

    # ----- Webhook / approval security -----
    # When true, HubSpot inbound webhook requires a verified v3 signature.
    # Set to false ONLY for local development against unsigned mock payloads.
    HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE: bool = True
    # Max age of a webhook's signed timestamp before we reject it (replay guard).
    # 300s tolerates a sleeping free-tier host's wake-up latency on the first
    # delivery; HubSpot's own tolerance is ~5 min. Lower it for stricter replay
    # protection on an always-on host.
    HUBSPOT_SIGNATURE_MAX_AGE_SECONDS: int = 300
    # When true, dump rejected webhook payloads to data/last_rejected_webhook.json
    # for offline signature debugging. Default OFF — the dump writes the request
    # body+headers to disk on an attacker-triggerable path, so only enable while
    # actively debugging. Secret material is never written regardless.
    WEBHOOK_DEBUG_DUMP: bool = False
    # Per-message HMAC token (signed with INTERNAL_API_TOKEN) protects /approve/{id} from IDOR.
    APPROVAL_REQUIRE_TOKEN: bool = True
    # Comma-separated CIDR or IPs whose X-Forwarded-For we trust.
    TRUSTED_PROXIES: str = ""
    # External base URL used in approval links (overrides APP_HOST:APP_PORT in prod).
    PUBLIC_BASE_URL: str = ""

    # ----- Web UI access (public deploy) -----
    # The web UI (approve/edit/reject, knowledge editing) has no per-action auth and
    # is localhost-only by default. To use it on a public deployment (e.g. Render),
    # set WEB_UI_PASSWORD — the UI then requires HTTP Basic Auth from any origin.
    # Leave empty to keep the localhost-only gate (local development).
    WEB_UI_USERNAME: str = "admin"
    WEB_UI_PASSWORD: str = ""

    # ----- Web UI auth mode -----
    # "basic"  : localhost-only, or HTTP Basic Auth when WEB_UI_PASSWORD is set (default).
    # "google_oauth" : Google sign-in restricted to ALLOWED_EMAIL_DOMAIN + an allowlist.
    # Until the Google credentials below are set, keep "basic" so the deploy never locks out.
    AUTH_MODE: Literal["basic", "google_oauth"] = "basic"
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    # Only verified emails on this domain may sign in (e.g. estsoft.com Google Workspace).
    ALLOWED_EMAIL_DOMAIN: str = "estsoft.com"
    # NOTE: there is deliberately no admin-email env var. Operators are managed only in
    # the `users` table (/settings/users); the first sign-in on an empty table bootstraps
    # an admin, and scripts/bootstrap_admin.py is the recovery path.
    # HMAC key for signing the session cookie. REQUIRED when AUTH_MODE=google_oauth.
    SESSION_SECRET: str = ""

    @property
    def LLM_PROVIDER(self) -> str:
        """The only LLM provider. Used as the draft/usage label. Not env-configurable."""
        return "gemini_vertex"


settings = Settings()
