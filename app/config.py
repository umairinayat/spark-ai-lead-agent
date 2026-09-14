"""Central configuration.

Every external dependency in this project is optional. The service is designed
so that `docker compose up` with an empty .env still produces a working,
demonstrable end-to-end pipeline:

  * No OPENAI_API_KEY   -> the agent falls back to a deterministic rule engine.
  * No CRM credentials  -> CRM_PROVIDER=mock writes to local SQLite.
  * No SLACK_WEBHOOK_URL-> notifications are logged and persisted instead.

That is a deliberate design decision, not a shortcut: it makes the automation
testable in CI and reviewable by someone who does not hold our credentials.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CrmProvider = Literal["mock", "gohighlevel", "hubspot"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---------------------------------------------------------------- service
    app_name: str = "Spark AI Lead Qualification Agent"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    database_path: str = Field(default="data/leads.db")

    # Shared secret required by mutating endpoints. Empty string disables the
    # check so the project runs out of the box; set it in any real deployment.
    api_key: str = Field(default="", alias="API_KEY")

    # Simple in-process rate limit for the public intake endpoint.
    rate_limit_requests: int = Field(default=30)
    rate_limit_window_seconds: int = Field(default=60)

    cors_allow_origins: str = Field(default="*")

    # ------------------------------------------------------------------- llm
    llm_provider: Literal["openai", "rules"] = Field(default="openai")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="gpt-4o-mini")
    llm_timeout_seconds: float = Field(default=30.0)
    llm_max_retries: int = Field(default=2)
    llm_temperature: float = Field(default=0.2)
    # Upper bound on tool-calling rounds before we force the final answer.
    agent_max_tool_rounds: int = Field(default=3)

    # ------------------------------------------------------------------- crm
    crm_provider: CrmProvider = Field(default="mock")

    # GoHighLevel (paid product -> entirely optional).
    ghl_api_key: str = Field(default="", alias="GHL_API_KEY")
    ghl_location_id: str = Field(default="", alias="GHL_LOCATION_ID")
    ghl_base_url: str = Field(default="https://services.leadconnectorhq.com")
    ghl_api_version: str = Field(default="2021-07-28")
    ghl_pipeline_id: str = Field(default="")
    ghl_stage_high: str = Field(default="")
    ghl_stage_medium: str = Field(default="")
    ghl_stage_low: str = Field(default="")

    # HubSpot (free developer tier -> usable substitute for GHL).
    hubspot_token: str = Field(default="", alias="HUBSPOT_TOKEN")
    hubspot_base_url: str = Field(default="https://api.hubapi.com")

    crm_timeout_seconds: float = Field(default=20.0)
    crm_max_retries: int = Field(default=2)

    # --------------------------------------------------------------- notify
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")
    slack_timeout_seconds: float = Field(default=10.0)
    notify_on_priorities: str = Field(default="High")

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------ properties
    @property
    def llm_enabled(self) -> bool:
        """True when a real LLM call is possible."""
        return self.llm_provider == "openai" and bool(self.openai_api_key)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_webhook_url)

    @property
    def notify_priorities(self) -> set[str]:
        return {p.strip() for p in self.notify_on_priorities.split(",") if p.strip()}

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    def ghl_stage_for(self, priority: str) -> str:
        return {
            "High": self.ghl_stage_high,
            "Medium": self.ghl_stage_medium,
            "Low": self.ghl_stage_low,
        }.get(priority, "")

    def describe(self) -> dict[str, object]:
        """Non-secret snapshot of the active configuration, used by /health."""
        return {
            "environment": self.environment,
            "llm_provider": self.llm_provider if self.llm_enabled else "rules",
            "llm_model": self.openai_model if self.llm_enabled else "deterministic-rules-v1",
            "llm_live": self.llm_enabled,
            "crm_provider": self.crm_provider,
            "slack_enabled": self.slack_enabled,
            "api_key_required": bool(self.api_key),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
