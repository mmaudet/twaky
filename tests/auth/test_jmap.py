"""Plume-facing wrapper on top of oidc.get_impersonated_token."""

from __future__ import annotations

from twaky.auth import jmap, oidc


def test_bearer_token_for_owner_uses_settings(monkeypatch):
    calls = {}

    def _fake_get(subject_email, **kw):
        calls["subject"] = subject_email
        calls["kw"] = kw
        return "TOKEN"

    monkeypatch.setattr(oidc, "get_impersonated_token", _fake_get)
    monkeypatch.setattr(jmap.settings, "twaky_owner_email", "alice@x")
    monkeypatch.setattr(jmap.settings, "plume_oidc_client_id", "cid")
    monkeypatch.setattr(jmap.settings, "plume_oidc_client_secret", "cs")
    monkeypatch.setattr(jmap.settings, "plume_oidc_issuer", "https://auth.x/")

    tok = jmap.bearer_token_for_owner()
    assert tok == "TOKEN"
    assert calls["subject"] == "alice@x"
    assert calls["kw"]["client_id"] == "cid"
