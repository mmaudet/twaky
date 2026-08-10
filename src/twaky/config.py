"""Runtime configuration for all twaky workers, loaded from environment."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Owner scoping (required — fail-fast if missing) ---
    twaky_owner_email: str = Field(
        ...,  # required — no default
        description="Email of the sole owner this instance serves.",
    )

    # --- Twaky Postgres+AGE ---
    twaky_pg_host: str = Field(default="twaky-pg")
    twaky_pg_port: int = Field(default=5432)
    twaky_pg_db: str = Field(default="twaky")
    twaky_pg_user: str = Field(default="twaky")
    twaky_pg_password: str = Field(default="twaky")
    twaky_graph_name: str = Field(default="twake")

    # --- RabbitMQ ---
    rabbitmq_url: str = Field(default="amqp://guest:guest@rabbitmq:5672/%2F")

    # --- Ingest ---
    agent_exchanges: str = Field(
        default=(
            "calendar:event:created,calendar:event:updated,calendar:event:request,"
            "calendar:event:deleted,calendar:event:cancel,calendar:event:reply,"
            "sabre:contact:created,sabre:contact:updated,sabre:contact:update,"
            "sabre:contact:deleted,"
            "mail:message:received,mail:message:expunged,"
            "mail:message:flags:updated,mail:message:moved"
        ),
        description="Comma-separated fanout exchanges to bind to.",
    )
    agent_queue: str = Field(default="agent.graph.ingest")
    agent_prefetch: int = Field(default=32)

    # --- LLM (LiteLLM) ---
    model: str = Field(default="claude-sonnet-4-5-20250929")
    litellm_api_base: str | None = Field(default=None)

    # --- Langfuse ---
    langfuse_host: str | None = Field(default=None)
    langfuse_project_id: str | None = Field(default=None)
    langfuse_public_key: str | None = Field(default=None)
    langfuse_secret_key: str | None = Field(default=None)
    langfuse_public_url: str | None = Field(default=None)

    # --- Sub-agent LLMs (each falls back to `model` if unset) ---
    atlas_model: str | None = Field(default=None)
    chronos_model: str | None = Field(default=None)
    plume_model: str | None = Field(default=None)
    iris_model: str | None = Field(default=None)

    # --- Atlas daemon limits ---
    atlas_max_concurrent_missions: int = Field(
        default=4,
        alias="twaky_atlas_max_concurrent_missions",
    )
    atlas_max_steps: int = Field(
        default=12,
        alias="twaky_atlas_max_steps",
    )
    atlas_mission_timeout_s: int = Field(
        default=300,
        alias="twaky_atlas_mission_timeout_s",
    )
    atlas_max_tokens: int = Field(
        default=100_000,
        alias="twaky_atlas_max_tokens",
    )

    # --- Sentinels framework ---
    # No explicit alias= here: pydantic-settings matches env vars
    # case-insensitively against the field name, so SENTINEL_TIMEOUT_S,
    # sentinel_timeout_s, etc. all resolve correctly.  The atlas_* fields above
    # carry explicit aliases because their env var names carry the "twaky_"
    # prefix that differs from the Python field name.
    sentinel_timeout_s: int = Field(default=60)
    sentinel_max_concurrent_events: int = Field(default=4)
    sentinel_run_retention_days: int = Field(default=30)

    # --- SP6b: JMAP OAuth ---
    twaky_secret_key: str = Field(default="")
    jmap_oauth_client_id: str = Field(default="twaky-mail-sentinel")
    jmap_oauth_client_secret: str = Field(default="")
    jmap_oauth_issuer: str = Field(default="https://auth.twake-dev.maudet.cloud")
    jmap_oauth_scope: str = Field(default="openid profile email offline_access")

    # --- JMAP polling event source ---
    jmap_session_url: str = Field(default="")
    jmap_bearer_token: str = Field(
        default=""
    )  # deprecated, ignored — SP6b uses oauth_credential table instead
    jmap_account_email: str = Field(default="")
    jmap_poll_interval_s: int = Field(default=60)

    # --- External services ---
    jmap_endpoint: str = Field(default="http://tmail-backend:8080/jmap")
    # TBD spec §13: fetch accountId dynamically via JMAP session call once the
    # tmail-backend session endpoint is confirmed.
    jmap_account_id: str = Field(default="")
    searxng_endpoint: str = Field(default="http://searxng:8080")

    # --- Plume OIDC token exchange (Twake Visio ↔ Calendar pattern) ---
    plume_oidc_client_id: str = Field(default="")
    plume_oidc_client_secret: str = Field(default="")
    plume_oidc_issuer: str = Field(default="")

    # --- Mail sentinel LLM tiers ---
    mail_sentinel_economy_llms: str = Field(
        default="openai/mlx-community/Qwen3-VL-8B-Instruct-4bit"
    )
    mail_sentinel_default_llms: str = Field(
        default="openai/mlx-community/Qwen3-VL-8B-Instruct-4bit"
    )
    mail_sentinel_chat_llms: str = Field(
        default="openai/mlx-community/Qwen3-VL-8B-Instruct-4bit"
    )
    mail_sentinel_draft_llms: str = Field(
        default="openai/mlx-community/Qwen3-VL-8B-Instruct-4bit"
    )

    # --- Mail sentinel LLM endpoint override ---
    # When non-empty, routes mail-sentinel LLM calls to an OpenAI-compatible
    # endpoint (e.g. a local MLX server) instead of the default LiteLLM provider
    # routing derived from the model prefix.
    mail_sentinel_api_base: str = Field(default="")
    mail_sentinel_api_key: str = Field(default="")

    # --- Sub-project 3a: API ---
    api_base_url: str = Field(default="http://twaky-api:8000")
    api_session_secret: str = Field(default="")
    api_oidc_client_id: str = Field(default="")
    api_oidc_client_secret: str = Field(default="")
    api_oidc_issuer: str = Field(default="")

    @property
    def exchanges(self) -> list[str]:
        return [x.strip() for x in self.agent_exchanges.split(",") if x.strip()]

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.twaky_pg_user}:{self.twaky_pg_password}"
            f"@{self.twaky_pg_host}:{self.twaky_pg_port}/{self.twaky_pg_db}"
        )


settings = Settings()  # type: ignore[call-arg]
