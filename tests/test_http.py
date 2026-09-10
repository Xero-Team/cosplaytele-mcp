import httpx
import pytest

from cosplaytele_mcp.http import HTTP_TIMEOUT, Http, OutboundUrlError


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


@pytest.mark.anyio
async def test_http_caches_successful_get() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"n": attempts["n"]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        http = Http(client)
        first = await http.get_json("https://example.com/posts")
        second = await http.get_json("https://example.com/posts")
    assert first == {"n": 1}
    assert second == {"n": 1}
    assert attempts["n"] == 1


@pytest.mark.anyio
async def test_http_cache_can_be_disabled() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, json={"n": attempts["n"]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://example.com") as client:
        http = Http(client, cache_ttl=0)
        await http.get_json("https://example.com/posts")
        await http.get_json("https://example.com/posts")
    assert attempts["n"] == 2


@pytest.mark.anyio
async def test_http_request_can_bypass_an_enabled_cache() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(200, content=b"image")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client)
        await http.get("https://example.com/image.webp", cache=False)
        await http.get("https://example.com/image.webp", cache=False)
    assert attempts["n"] == 2


@pytest.mark.anyio
async def test_http_inherits_the_client_timeout_by_default() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=HTTP_TIMEOUT
    ) as client:
        await Http(client).get("https://example.com/posts")
    assert seen["timeout"] == {
        "connect": 15.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }


@pytest.mark.anyio
async def test_http_rejects_redirects_outside_source_allowlist() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.org/redirected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client, allowed_hosts=("https://cosplaytele.com",))
        with pytest.raises(OutboundUrlError, match="not an allowed source"):
            await http.get("https://cosplaytele.com/probe/")
    assert requested == ["https://cosplaytele.com/probe/"]
