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

    # ----- LLM -----
    LLM_PROVIDER: Literal["claude_cli", "anthropic_api"] = "claude_cli"
    CLAUDE_CLI_PATH: str = "claude"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # ----- HubSpot -----
    HUBSPOT_PRIVATE_APP_TOKEN: str = ""
    HUBSPOT_INBOUND_PIPELINE_ID: str = ""
    HUBSPOT_OWNER_ID: str = ""
    HUBSPOT_WEBHOOK_SECRET: str = ""

    # ----- Email -----
    EMAIL_PROVIDER: Literal["hubspot", "smtp"] = "hubspot"

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Sales Team"
    SMTP_FROM_EMAIL: str = ""

    # ----- WhatsApp -----
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_TEMPLATE_NAME: str = "sales_reply_intro"

    # ----- Approval -----
    APPROVAL_CHANNEL: Literal["slack", "teams", "none"] = "slack"
    SLACK_BOT_TOKEN: str = ""
    SLACK_APPROVAL_CHANNEL_ID: str = ""
    TEAMS_WEBHOOK_URL: str = ""

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
    DAILY_SEND_LIMIT: int = 100
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

    # 빈 문자열이면 auth 미들웨어가 모든 요청을 거부합니다(보안). 운영 전 반드시 강한 토큰 설정.
    INTERNAL_API_TOKEN: str = Field(default="")

    # ----- Webhook / approval security -----
    # When true, HubSpot inbound webhook requires a verified v3 signature.
    # Set to false ONLY for local development against unsigned mock payloads.
    HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE: bool = True
    HUBSPOT_SIGNATURE_MAX_AGE_SECONDS: int = 60
    # Per-message HMAC token (signed with INTERNAL_API_TOKEN) protects /approve/{id} from IDOR.
    APPROVAL_REQUIRE_TOKEN: bool = True
    # Comma-separated CIDR or IPs whose X-Forwarded-For we trust.
    TRUSTED_PROXIES: str = ""
    # External base URL used in unsubscribe / approval links (overrides APP_HOST:APP_PORT in prod).
    PUBLIC_BASE_URL: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — use this everywhere instead of constructing Settings()."""
    return Settings()


settings = get_settings()
