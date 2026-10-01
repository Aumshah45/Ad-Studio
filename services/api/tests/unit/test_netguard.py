import socket

import httpx
import pytest

from tests.netguard import NetworkBlockedError


def test_no_network() -> None:
    """The autouse guard blocks outbound connects and DNS; loopback stays usable."""
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("generativelanguage.googleapis.com", 443)
    with pytest.raises(NetworkBlockedError), httpx.Client() as client:
        client.get("https://generativelanguage.googleapis.com/v1beta/models")

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        with socket.create_connection(server.getsockname(), timeout=1):
            pass
    finally:
        server.close()


async def test_no_network_async_client() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(NetworkBlockedError):
            await client.get("https://api.groq.com/openai/v1/models")
