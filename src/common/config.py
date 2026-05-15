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

    # ----- Approval -----
    APPROVAL_CHANNEL: Literal["slack", "teams", "none"] = "slack"
    SLACK_BOT_TOKEN: str = ""
    SLACK_APPROVAL_CHANNEL_ID: str = ""
    TEAMS_WEBHOOK_URL: str = ""

    # ----- Reports -----
    REPORT_SLACK_CHANNEL_ID: str = ""
    REPORT_EMAIL_TO: str = ""

    # ----- Sources -----
    YOUTUBE_API_KEY: str = ""
    LINKEDIN_ENABLED: bool = False
    LINKEDIN_SCRAPING_ENABLED: bool = False
    LINKEDIN_SESSION_COOKIE: str = ""
    LINKEDIN_API_TOKEN: str = ""

    # ----- Inbound poller -----
    INBOUND_POLL_ENABLED: bool = False
    INBOUND_POLL_INTERVAL_SECONDS: int = 600

    # ----- Behavior knobs -----
    AUTO_SEND_THRESHOLD: float = 1.01  # >1.0 => never auto-send
    OUTBOUND_COOLDOWN_DAYS: int = 90
    FOLLOWUP_AFTER_DAYS: int = 4
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

    # 빈 문자열이면 auth 미들웨어가 모든 요청을 거부합니다(보안). 운영 전 반드시 강한 토큰 설정.
    INTERNAL_API_TOKEN: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — use this everywhere instead of constructing Settings()."""
    return Settings()


settings = get_settings()
