"""RFC 9457 problem+json errors for every non-2xx response."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
log = structlog.get_logger(__name__)


class AppError(Exception):
    def __init__(self, status: int, type: str, title: str, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or title)
        self.status = status
        self.type = type
        self.title = title
        self.detail = detail
        self.extra = extra


def _request_id(request: Request | None) -> str | None:
    if request is None:
        return None
    return getattr(request.state, "request_id", None)


def problem_body(
    status: int,
    type: str,
    title: str,
    detail: str = "",
    request_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"type": type, "title": title, "status": status, "detail": detail}
    if request_id:
        body["request_id"] = request_id
    body.update(extra)
    return body


def problem_response(
    status: int,
    type: str,
    title: str,
    detail: str = "",
    request: Request | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body = problem_body(status, type, title, detail, _request_id(request), **extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101
    retry_after = exc.extra.get("retry_after")
    headers = {"Retry-After": str(int(retry_after))} if isinstance(retry_after, int) else None
    return problem_response(
        exc.status, exc.type, exc.title, exc.detail, request, headers=headers, **exc.extra
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": [str(p) for p in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in exc.errors()
    ]
    return problem_response(
        422,
        "validation-error",
        "Invalid request",
        "Request failed validation.",
        request,
        errors=errors,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    headers = dict(exc.headers) if exc.headers else None
    return problem_response(
        exc.status_code,
        f"http-{exc.status_code}",
        str(exc.detail),
        str(exc.detail),
        request,
        headers=headers,
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_error", error_type=type(exc).__name__, exc_info=exc)
    # Runs in ServerErrorMiddleware (outside our middlewares), so add their headers here.
    headers = dict(SECURITY_HEADERS)
    rid = _request_id(request)
    if rid:
        headers["X-Request-ID"] = rid
    return problem_response(
        500,
        "internal-error",
        "Internal server error",
        "An unexpected error occurred.",
        request,
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
