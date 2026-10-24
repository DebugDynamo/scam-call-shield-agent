"""Contract-shaped errors (docs/API_CONTRACTS.md "Error response")."""

from __future__ import annotations

import contextvars
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

STATUS_BY_CODE = {
    "VALIDATION_FAILED": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "PROVIDER_ERROR": 502,
    "PROVIDER_NOT_CONFIGURED": 503,
    "PROVIDER_RATE_LIMITED": 429,
    "TIMEOUT": 504,
    "DATABASE_UNAVAILABLE": 503,
    "INTERNAL": 500,
    # extension (not in the v1 contract): room mode is an unimplemented adapter
    "NOT_IMPLEMENTED": 501,
}


class AppError(Exception):
    def __init__(self, code: str, message: str, details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    @property
    def status(self) -> int:
        return STATUS_BY_CODE[self.code]


def validation_failed(message: str, path: str = "", detail: str | None = None) -> AppError:
    return AppError("VALIDATION_FAILED", message, [{"path": path, "message": detail or message}])


def not_found(what: str, ident: str) -> AppError:
    return AppError("NOT_FOUND", f"{what} {ident} not found")


def provider_not_configured(provider: str, env_var: str, extra: str = "") -> AppError:
    return AppError(
        "PROVIDER_NOT_CONFIGURED",
        f"Provider '{provider}' is not configured: set {env_var}{extra}",
        [{"path": f"env.{env_var}", "message": "missing"}],
    )


def error_body(code: str, message: str, details: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "requestId": request_id_var.get(),
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
    }


def _json(code: str, message: str, details: list[dict[str, str]] | None = None) -> JSONResponse:
    return JSONResponse(
        error_body(code, message, details),
        status_code=STATUS_BY_CODE[code],
        headers={"X-Request-Id": request_id_var.get()},
    )


_HTTP_TO_CODE = {400: "VALIDATION_FAILED", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        if exc.status >= 500:
            logger.warning("{} {}", exc.code, exc.message)
        return _json(exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {"path": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
        ]
        return _json("VALIDATION_FAILED", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException):
        code = _HTTP_TO_CODE.get(exc.status_code)
        if code is None:
            code = "INTERNAL" if exc.status_code >= 500 else "VALIDATION_FAILED"
        resp = _json(code, str(exc.detail))
        resp.status_code = exc.status_code
        return resp

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error: {}", type(exc).__name__)
        return _json("INTERNAL", "Internal server error")


class RequestIdMiddleware:
    """Pure ASGI middleware: honour/generate X-Request-Id, echo it on the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        rid = None
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                rid = v.decode("latin-1")[:128]
        rid = rid or str(uuid.uuid4())
        token = request_id_var.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
