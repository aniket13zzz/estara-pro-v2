"""Deployment settings and secrets, read from environment variables / ``.env``.

Behaviour lives in ``client/client.config.yaml``; this module only holds infrastructure
settings and secrets. Every variable here is listed in ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+psycopg://estara:estara@localhost:5432/estara"
    redis_url: str = "redis://localhost:6379/0"
    # Free cloud demo (docs/deploy-free.md): no Redis, no separate worker. REDIS_ENABLED=false
    # uses the in-memory login rate limit; RUN_JOBS_IN_WEB=true runs the worker's daily jobs
    # (staleness check, retention, demo-data refresh) inside the web process instead.
    redis_enabled: bool = True
    run_jobs_in_web: bool = False
    # Public demo: synthetic data may load even with ENVIRONMENT=production, and calls are kept
    # short. Never set on a client's deployment.
    demo_mode: bool = False
    demo_max_call_minutes: int = 5

    session_secret: SecretStr = Field(default=SecretStr(""))
    session_ttl_hours: int = 12
    cookie_secure: bool = True
    dashboard_origin: str = "http://localhost:3000"

    # Login protection (identical for every client, so not in client config).
    login_ip_max_attempts: int = 20
    login_ip_window_seconds: int = 300
    login_lockout_threshold: int = 5
    login_lockout_minutes: int = 15

    # Data Center uploads (files are stored by SHA-256, never under the uploaded name).
    upload_dir: str = "uploads"
    max_upload_mb_data: int = 10
    max_upload_mb_document: int = 25

    # LLM calls (same for every client, so not in client config).
    # Seconds to wait for a whole (non-streamed) LLM response, thinking included.
    llm_timeout_seconds: float = 25.0
    # Attempts for a request that timed out (2 = one retry).
    llm_timeout_attempts: int = 2
    llm_max_attempts: int = 3
    # 429 handling for live conversations: retries, and the longest single wait (seconds)
    # before handing the customer to a person instead. The eval runner sets its own.
    llm_rate_limit_attempts: int = 3
    llm_max_rate_limit_wait_seconds: float = 10.0
    # Minimum gap between LLM calls (seconds). 0 in production; free tiers need a few seconds.
    llm_min_interval_seconds: float = 0.0

    # Provider secrets. Optional until the stage that uses them.
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    vapi_api_key: SecretStr | None = None
    # Shared secret Vapi sends with every request to us (Authorization: Bearer <secret>, or the
    # legacy X-Vapi-Secret header). Set the same value in Vapi as a Bearer credential (server
    # URL) and as the Custom LLM API key. See docs/demo.md.
    vapi_webhook_secret: SecretStr | None = None
    vapi_public_key: str | None = None  # browser key for dashboard test calls (Vapi "public key")
    vapi_assistant_id: str | None = None  # printed by `python -m app.channels.vapi.sync`
    # Comparison assistant B (Vapi API Request tools), printed by `...vapi.api_tools` (ADR 0015).
    vapi_assistant_id_b: str | None = None
    vapi_server_credential_id: str | None = None  # the Bearer credential's id in Vapi
    # The address Vapi uses to reach this backend (a free tunnel for demos, the client's domain
    # in production), e.g. https://ai.clientname.in
    public_base_url: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str = "v24.0"  # Graph API version used for sending

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, value: str) -> str:
        """Hosted Postgres (Neon, Supabase, Render) hands out ``postgresql://`` or
        ``postgres://`` URLs; ESTARA's async engine needs the psycopg driver named."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @field_validator("session_secret")
    @classmethod
    def _secret_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError(
                "SESSION_SECRET must be set to at least 32 random characters "
                "(e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
