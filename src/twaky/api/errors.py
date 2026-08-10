"""Uniform error envelope + FastAPI exception handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def error_response(
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
    status_code: int = 400,
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body)


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(
        code=f"http_{exc.status_code}",
        message=str(exc.detail) if exc.detail else "error",
        status_code=exc.status_code,
    )


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Return pydantic v2 error dicts with non-serializable ctx.error coerced to str."""
    safe: list[dict[str, Any]] = []
    for err in exc.errors():
        entry: dict[str, Any] = dict(err)
        # pydantic v2 ctx.error is a Python exception — not JSON-serializable
        if "ctx" in entry and "error" in entry["ctx"]:
            entry["ctx"] = dict(entry["ctx"])
            entry["ctx"]["error"] = str(entry["ctx"]["error"])
        # loc is a tuple; convert to list for JSON serialization
        if "loc" in entry:
            entry["loc"] = list(entry["loc"])
        # drop url key (not needed in API responses)
        entry.pop("url", None)
        safe.append(entry)
    return safe


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        code="validation_error",
        message="request body failed validation",
        detail={"errors": _safe_errors(exc)},
        status_code=422,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]


__all__ = ["error_response", "register_exception_handlers"]
