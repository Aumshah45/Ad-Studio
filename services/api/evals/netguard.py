"""Process-level socket guard for `make eval`: non-loopback connects and DNS lookups raise and are
counted (the report states the count). The local Postgres (loopback, or libpq's own C sockets)
stays reachable."""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})  # noqa: S104


class NetworkBlockedError(OSError):
    pass


@dataclass
class NetworkGuard:
    attempts: list[str] = field(default_factory=list[str])

    def check(self, host: str | None) -> None:
        if host is None or host.lower() in LOOPBACK or host.startswith("127."):
            return
        self.attempts.append(host)
        raise NetworkBlockedError(f"network access to {host!r} is blocked during make eval")


def _host(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None


@contextmanager
def block_network() -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        if self.family != socket.AF_UNIX:
            guard.check(_host(address))
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family != socket.AF_UNIX:
            guard.check(_host(address))
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        guard.check(
            host.decode() if isinstance(host, bytes) else (None if host is None else str(host))
        )
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        yield guard
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
