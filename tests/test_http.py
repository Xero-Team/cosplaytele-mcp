import httpx
import pytest

from cosplaytele_mcp.http import Http


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_http_retries_on_503() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        payload = await Http(client).get_json("https://example.com/posts")
    assert payload == {"ok": True}
    assert attempts["n"] == 3


@pytest.mark.anyio
async def test_http_does_not_retry_404() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await Http(client).get("https://example.com/missing")
    assert attempts["n"] == 1
