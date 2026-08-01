"""Uniform error envelope from FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from twaky.api.errors import error_response, register_exception_handlers


class Body(BaseModel):
    name: str
    age: int


def _app_with_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-http")
    def _raise_http():
        raise HTTPException(status_code=409, detail="already exists")

    @app.post("/validate")
    def _validate(body: Body) -> dict:
        return {"got": body.model_dump()}

    return app


class TestErrorEnvelope:
    def test_http_exception_is_wrapped(self):
        client = TestClient(_app_with_handlers())
        r = client.get("/raise-http")
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "http_409"
        assert body["error"]["message"] == "already exists"

    def test_validation_error_returns_422(self):
        client = TestClient(_app_with_handlers())
        r = client.post("/validate", json={"name": "x"})
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "validation_error"
        assert "message" in body["error"]

    def test_error_response_helper_builds_envelope(self):
        resp = error_response(
            "bad_thing", "human message", {"key": "v"}, status_code=418
        )
        assert resp.status_code == 418
        import json

        payload = json.loads(resp.body)
        assert payload == {
            "error": {
                "code": "bad_thing",
                "message": "human message",
                "detail": {"key": "v"},
            }
        }
