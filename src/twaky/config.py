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
    langfuse_public_key: str | None = Field(default=None)
    langfuse_secret_key: str | None = Field(default=None)
    langfuse_public_url: str | None = Field(default=None)

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
