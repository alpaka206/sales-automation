"""
Settings — loads from `.env` via pydantic-settings.

Every value has a default so importing this module never crashes,
even before the operator fills in `.env`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # ----- LLM (Gemini on Vertex AI — the only provider) -----
    # Hybrid model tiers:
    #   GEMINI_MODEL      → fast/cheap "flash" tier for light judgment
    #                       (classification, scoring, doc routing, enrichment).
    #   GEMINI_MODEL_PRO  → high-quality "pro" tier for customer-facing drafting
    #                       (inbound reply drafts, outbound opening emails).
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
    HUBSPOT_PRIVATE_APP_TOKEN: str = ""
    HUBSPOT_INBOUND_PIPELINE_ID: str = ""
    HUBSPOT_OWNER_ID: str = ""
    HUBSPOT_WEBHOOK_SECRET: str = ""
    # After SMTP send completes, move the linked HubSpot ticket to this pipeline stage
    # id (e.g. "문의 대기"). Empty = don't touch stage. Find the id in
    # HubSpot Settings → Objects → Tickets → Pipelines (click a stage → copy id).
    HUBSPOT_TICKET_STAGE_AFTER_SEND: str = ""
    # Set to true ONLY if the HubSpot account has a custom `inbound_status`
    # text property on contacts. We write "analyzed" / "meeting_link_sent" to
    # it for operator visibility, but the value is never read back, and the
    # ticket pipeline_stage covers the same role in the new workflow. Default
    # off — keeping it on without the property logs a 400 every webhook.
    HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS: bool = False

    # ----- Email -----
    EMAIL_PROVIDER: Literal["hubspot", "smtp"] = "hubspot"

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Sales Team"
    SMTP_FROM_EMAIL: str = ""

    # ----- Test mode: redirect ALL real sends to one address -----
    # When non-empty, every customer-facing email (inbound reply, outbound open,
    # follow-up) is rerouted to this address and sent via SMTP — the HubSpot
    # provider is bypassed so nothing lands on a real contact's timeline, and the
    # WhatsApp piggyback is skipped so no real phone is messaged. The original
    # intended recipient is preserved in the subject as "[TEST→original]".
    # Requires SMTP_USERNAME/SMTP_PASSWORD. Leave EMPTY in production.
    SEND_OVERRIDE_EMAIL: str = ""

    # ----- WhatsApp -----
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_TEMPLATE_NAME: str = "sales_reply_intro"

    # ----- Approval -----
    APPROVAL_CHANNEL: Literal["slack", "none"] = "slack"
    SLACK_BOT_TOKEN: str = ""
    SLACK_APPROVAL_CHANNEL_ID: str = ""

    # ----- Reports -----
    REPORT_SLACK_CHANNEL_ID: str = ""
    REPORT_EMAIL_TO: str = ""

    # ----- Gmail IMAP (reply detection for SMTP-sent mail) -----
    GMAIL_IMAP_USERNAME: str = ""
    GMAIL_IMAP_PASSWORD: str = ""
    GMAIL_IMAP_FOLDER: str = "INBOX"

    # ----- Sources -----
    YOUTUBE_API_KEY: str = ""
    GOOGLE_CSE_API_KEY: str = ""
    GOOGLE_CSE_ID: str = ""
    JOB_BOARD_SITES: str = "saramin.co.kr,jobkorea.co.kr"
    LINKEDIN_ENABLED: bool = False
    LINKEDIN_SCRAPING_ENABLED: bool = False
    LINKEDIN_SESSION_COOKIE: str = ""
    LINKEDIN_API_TOKEN: str = ""

    # ----- Inbound poller -----
    INBOUND_POLL_ENABLED: bool = False
    INBOUND_POLL_INTERVAL_SECONDS: int = 600

    # ----- Send worker -----
    SEND_WORKER_ENABLED: bool = False
    SEND_RATE_PER_MINUTE: int = 5
    # Safety cap on outbound emails/day. Not about "internal vs external" — it guards
    # the Gmail/SMTP sender quota (free Gmail ~500/day; exceeding throttles the account)
    # and sender reputation, and is the only backstop once auto-send/auto-followup is on.
    # Set generously rather than disabling (0 = unlimited). 400 ≈ free-Gmail margin.
    DAILY_SEND_LIMIT: int = 400
    SEND_JITTER_SECONDS: int = 15

    # ----- Behavior knobs -----
    AUTO_SEND_THRESHOLD: float = 1.01  # >1.0 => never auto-send
    OUTBOUND_COOLDOWN_DAYS: int = 90
    FOLLOWUP_AFTER_DAYS: int = 7
    FOLLOWUP_AUTO_SEND: bool = False
    MAX_FOLLOWUPS_PER_PROSPECT: int = 2
    ICP_THRESHOLD: int = 50
    DAILY_REPORT_HOUR: int = 18
    WEEKLY_REPORT_DOW: int = 5  # 0=Mon, 5=Sat

    # ----- DB -----
    DATABASE_URL: str = "sqlite:///./data/app.db"

    # ----- App -----
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Seoul"

    # ----- Company info (compliance footer) -----
    COMPANY_NAME: str = "perso"
    COMPANY_REGISTRATION_NUMBER: str = ""
    COMPANY_ADDRESS: str = ""
    COMPANY_PRIVACY_POLICY_URL: str = ""
    KOREA_AD_PREFIX_ENABLED: bool = False

    # ----- Google Sheets (inbound mirror) -----
    # Optional: append every processed inbound inquiry as a row to a Google
    # Sheet for at-a-glance tracking. Uses a SEPARATE Google credential from
    # Vertex AI — a dedicated service-account JSON whose client email must be
    # granted edit access to the target spreadsheet (share the sheet with it).
    GSHEETS_ENABLED: bool = False
    GOOGLE_SHEETS_CREDENTIALS_JSON: str = ""
    GOOGLE_SHEETS_SPREADSHEET_ID: str = ""
    GOOGLE_SHEETS_INBOUND_TAB: str = "Inbound"

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
    # External base URL used in unsubscribe / approval links (overrides APP_HOST:APP_PORT in prod).
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
    AUTH_MODE: str = "basic"
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    # Only verified emails on this domain may sign in (e.g. estsoft.com Google Workspace).
    ALLOWED_EMAIL_DOMAIN: str = "estsoft.com"
    # Bootstrap admins (comma-separated emails): auto-approved with role=admin on first login.
    WEB_UI_ADMIN_EMAILS: str = ""
    # Static allowlist (comma-separated emails) approved without an admin action.
    WEB_UI_ALLOWED_EMAILS: str = ""
    # HMAC key for signing the session cookie. REQUIRED when AUTH_MODE=google_oauth.
    SESSION_SECRET: str = ""

    @property
    def LLM_PROVIDER(self) -> str:
        """The only LLM provider. Used as the draft/usage label. Not env-configurable."""
        return "gemini_vertex"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — use this everywhere instead of constructing Settings()."""
    return Settings()


settings = get_settings()
