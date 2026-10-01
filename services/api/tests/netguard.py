"""Socket guard for tests: any connection or DNS lookup to a non-loopback host raises.

Loopback (the local Postgres) and unix sockets stay allowed. psycopg's libpq connects in C and never
goes through here, which is fine because it only ever reaches the local test database.
"""

import socket
from typing import Any

import pytest

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})  # noqa: S104


class NetworkBlockedError(RuntimeError):
    pass


def _host_of(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])  # pyright: ignore[reportUnknownArgumentType]
    return None  # unix socket path or other non-IP address


def _check(host: str | None) -> None:
    if host is not None and host.lower() not in LOOPBACK_HOSTS and not host.startswith("127."):
        raise NetworkBlockedError(f"network access to {host!r} is blocked in tests")


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        if self.family != socket.AF_UNIX:
            _check(_host_of(address))
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family != socket.AF_UNIX:
            _check(_host_of(address))
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _check(host.decode() if isinstance(host, bytes) else (None if host is None else str(host)))
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
