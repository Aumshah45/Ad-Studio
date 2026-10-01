"""Pure-ASGI middlewares: request id, body size limit, rate limit, security headers."""

import json
import re
import time
import uuid
from dataclasses import dataclass

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.errors import PROBLEM_JSON, SECURITY_HEADERS, problem_body

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def send_problem(
    send: Send,
    status: int,
    type: str,
    title: str,
    detail: str,
    request_id: str | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(problem_body(status, type, title, detail, request_id)).encode()
    raw_headers = [
        (b"content-type", PROBLEM_JSON.encode()),
        (b"content-length", str(len(body)).encode()),
        *(headers or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _rid(scope: Scope) -> str | None:
    return scope.get("state", {}).get("request_id")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id")
        rid = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = _header(scope, b"content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            await self._reject(send, scope)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send, scope)

    async def _reject(self, send: Send, scope: Scope) -> None:
        await send_problem(
            send,
            413,
            "payload-too-large",
            "Payload too large",
            f"Request body exceeds {self.max_bytes} bytes.",
            _rid(scope),
        )


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimitMiddleware:
    """In-process token bucket per client IP (use Redis when running more than one instance)."""

    EXEMPT = frozenset({"/health", "/ready"})

    def __init__(self, app: ASGIApp, per_minute: int) -> None:
        self.app = app
        self.capacity = float(max(per_minute, 1))
        self.rate = self.capacity / 60.0
        self.buckets: dict[str, _Bucket] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or scope.get("path") in self.EXEMPT
        ):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        now = time.monotonic()
        bucket = self.buckets.setdefault(ip, _Bucket(self.capacity, now))
        bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        if bucket.tokens < 1:
            retry = max(1, int((1 - bucket.tokens) / self.rate) + 1)
            await send_problem(
                send,
                429,
                "rate-limited",
                "Too many requests",
                f"Rate limit of {int(self.capacity)} requests per minute exceeded.",
                _rid(scope),
                headers=[(b"retry-after", str(retry).encode())],
            )
            return
        bucket.tokens -= 1
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    HEADERS = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(self.HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
