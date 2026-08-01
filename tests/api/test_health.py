"""GET /healthz is public and returns 200."""


class TestHealthz:
    def test_returns_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_no_auth_required(self, client):
        # No cookie, no session — must still succeed.
        r = client.get("/healthz")
        assert r.status_code == 200
