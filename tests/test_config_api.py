"""Config validation for sub-project 3a API settings."""

from __future__ import annotations

from twaky.config import Settings


def _s(monkeypatch, **extra) -> Settings:
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "a@x")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestApiSettings:
    def test_api_base_url_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.api_base_url == "http://twaky-api:8000"

    def test_api_base_url_override(self, monkeypatch):
        s = _s(monkeypatch, API_BASE_URL="https://twaky.example.com")
        assert s.api_base_url == "https://twaky.example.com"

    def test_session_secret_empty_by_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.api_session_secret == ""

    def test_oidc_fields_empty_by_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.api_oidc_client_id == ""
        assert s.api_oidc_client_secret == ""
        assert s.api_oidc_issuer == ""

    def test_oidc_fields_read_env(self, monkeypatch):
        s = _s(
            monkeypatch,
            API_OIDC_CLIENT_ID="twaky-api",
            API_OIDC_CLIENT_SECRET="s3cret",
            API_OIDC_ISSUER="https://auth.twake-dev.maudet.cloud/",
        )
        assert s.api_oidc_client_id == "twaky-api"
        assert s.api_oidc_client_secret == "s3cret"
        assert s.api_oidc_issuer == "https://auth.twake-dev.maudet.cloud/"


class TestLangfuseSettings:
    def test_langfuse_project_id_default(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PROJECT_ID", raising=False)
        s = _s(monkeypatch)
        assert s.langfuse_project_id is None

    def test_langfuse_host_and_project_id_override(self, monkeypatch):
        s = _s(
            monkeypatch,
            LANGFUSE_HOST="https://langfuse.example.com",
            LANGFUSE_PROJECT_ID="my-project",
        )
        assert s.langfuse_host == "https://langfuse.example.com"
        assert s.langfuse_project_id == "my-project"
