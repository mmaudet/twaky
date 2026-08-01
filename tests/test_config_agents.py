"""Config validation for sub-project 2 agent + daemon settings."""

from __future__ import annotations

from twaky.config import Settings


def _s(monkeypatch, **extra) -> Settings:
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "a@x")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestModelFallbacks:
    def test_all_agent_models_optional(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.atlas_model is None
        assert s.chronos_model is None
        assert s.plume_model is None
        assert s.iris_model is None

    def test_agent_models_read_env(self, monkeypatch):
        s = _s(
            monkeypatch, ATLAS_MODEL="openai/gpt-5", PLUME_MODEL="openai/gpt-4o-mini"
        )
        assert s.atlas_model == "openai/gpt-5"
        assert s.plume_model == "openai/gpt-4o-mini"


class TestDaemonDefaults:
    def test_max_concurrent_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.atlas_max_concurrent_missions == 4

    def test_max_concurrent_override(self, monkeypatch):
        s = _s(monkeypatch, TWAKY_ATLAS_MAX_CONCURRENT_MISSIONS="8")
        assert s.atlas_max_concurrent_missions == 8

    def test_step_limit_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.atlas_max_steps == 12

    def test_mission_timeout_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.atlas_mission_timeout_s == 300


class TestExternalEndpoints:
    def test_jmap_endpoint_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.jmap_endpoint == "http://tmail-backend:8080/jmap"

    def test_searxng_endpoint_default(self, monkeypatch):
        s = _s(monkeypatch)
        assert s.searxng_endpoint == "http://searxng:8080"

    def test_plume_oidc_required_together(self, monkeypatch):
        # No default — plume tools raise clearly when unset; keep as empty strings.
        s = _s(monkeypatch)
        assert s.plume_oidc_client_id == ""
        assert s.plume_oidc_client_secret == ""
        assert s.plume_oidc_issuer == ""
