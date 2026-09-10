import asyncio

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, PromptReference, ResourceTemplateReference, TextContent

from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import Gallery, ImageAsset, ListingItem, ListingPage, SourceId
from cosplaytele_mcp.server import _image_content, mcp
from cosplaytele_mcp.sources import SourceError


class StubSearchSource:
    supports_search = True
    category_names: tuple[str, ...] = ()

    def __init__(
        self, source_id: SourceId, outcome: ListingPage | Exception, *, delay: float = 0
    ) -> None:
        self.id = source_id
        self.outcome = outcome
        self.delay = delay

    async def search(
        self, query: str, page: int, category: str | None, *, exclude_ai: bool
    ) -> ListingPage:
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        return Gallery(
            source=self.id,
            title="Probe",
            path=path,
            url=f"https://example.com{path}",
            image_urls=["https://example.com/probe.webp"],
            image_count=1,
        )


class StubSearchRegistry:
    def __init__(self, sources: list[StubSearchSource]) -> None:
        self.sources = {source.id: source for source in sources}

    def get(self, source_id: SourceId) -> StubSearchSource:
        return self.sources[source_id]

    def all(self) -> list[StubSearchSource]:
        return list(self.sources.values())


def search_page(source: SourceId, title: str, *, has_next_page: bool = False) -> ListingPage:
    return ListingPage(
        source=source,
        page=1,
        has_next_page=has_next_page,
        items=[ListingItem(title=title, path=f"/{title}/", url=f"https://example.com/{title}/")],
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as connected:
        yield connected


@pytest.mark.anyio
async def test_list_sources(client: Client) -> None:
    result = await client.call_tool("list_sources", {})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    names = {item["id"] for item in payload["result"]}
    assert names == {
        "cosplaytele",
        "hentaicosplay",
        "everia",
        "misskon",
        "fourkhd",
        "kiutaku",
        "cup2d",
        "beauty3600000",
        "foamgirl",
        "ososedki",
        "mitaku",
        "lovecutes",
    }


@pytest.mark.anyio
async def test_list_tools_and_resources(client: Client) -> None:
    tools = await client.list_tools()
    tool_names = {tool.name for tool in tools.tools}
    assert {
        "list_sources",
        "browse",
        "search",
        "get_gallery",
        "open_url",
        "browse_tag",
        "related",
        "fetch_image",
    } <= tool_names
    by_name = {tool.name: tool for tool in tools.tools}
    assert by_name["list_sources"].annotations is not None
    assert by_name["list_sources"].annotations.read_only_hint is True
    assert by_name["list_sources"].annotations.open_world_hint is False
    assert by_name["search"].annotations is not None
    assert by_name["search"].annotations.read_only_hint is True
    assert by_name["search"].annotations.open_world_hint is True
    assert by_name["search"].annotations.idempotent_hint is None
    assert by_name["fetch_image"].annotations is not None
    assert by_name["fetch_image"].annotations.read_only_hint is True
    resources = await client.list_resources()
    uris = {str(resource.uri) for resource in resources.resources}
    assert "sources://catalog" in uris
    templates = await client.list_resource_templates()
    assert any("gallery://" in template.uri_template for template in templates.resource_templates)


@pytest.mark.anyio
async def test_image_content_uses_asset_referer_and_is_not_cached() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "image/webp"}, content=b"webp")

    asset = ImageAsset(
        url="https://static.example.com/image.webp",
        headers={"Referer": "https://example.com/"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client, allowed_hosts=("https://example.com",))
        first = await _image_content(http, asset)
        second = await _image_content(http, asset)
    assert isinstance(first, ImageContent)
    assert first.mime_type == "image/webp"
    assert first.data == "d2VicA=="
    assert second.data == first.data
    assert len(requests) == 2
    assert requests[0].headers["referer"] == "https://example.com/"


@pytest.mark.anyio
async def test_image_content_rejects_non_image_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"blocked")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client, allowed_hosts=("https://example.com",))
        with pytest.raises(ToolError, match="unsupported image content type"):
            await _image_content(http, ImageAsset(url="https://example.com/blocked"))


@pytest.mark.anyio
async def test_image_content_enforces_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"large")

    monkeypatch.setattr("cosplaytele_mcp.server.MAX_IMAGE_CONTENT_BYTES", 4)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client, allowed_hosts=("https://example.com",))
        with pytest.raises(ToolError, match="fetch_image supports at most 4 bytes"):
            await _image_content(http, ImageAsset(url="https://example.com/image.png"))


@pytest.mark.anyio
async def test_browse_rejects_page_zero(client: Client) -> None:
    result = await client.call_tool("browse", {"source": "cosplaytele", "page": 0})
    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)


@pytest.mark.anyio
async def test_browse_rejects_page_too_large(client: Client) -> None:
    result = await client.call_tool("browse", {"source": "cosplaytele", "page": 101})
    assert result.is_error is True


@pytest.mark.anyio
async def test_search_rejects_blank_query(client: Client) -> None:
    result = await client.call_tool("search", {"query": "   "})
    assert result.is_error is True


@pytest.mark.anyio
async def test_search_returns_single_source_results(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = StubSearchRegistry(
        [StubSearchSource("cosplaytele", search_page("cosplaytele", "Miku"))]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "miku", "source": "cosplaytele"})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    assert [item["title"] for item in payload["items"]] == ["Miku"]


@pytest.mark.anyio
async def test_search_labels_age_coded_costume_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = StubSearchRegistry(
        [StubSearchSource("cosplaytele", search_page("cosplaytele", "JK Uniform"))]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "jk", "source": "cosplaytele"})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    assert [item["title"] for item in payload["items"]] == ["JK (18+) Uniform (18+)"]


@pytest.mark.anyio
async def test_search_keeps_successful_sources_when_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = StubSearchRegistry(
        [
            StubSearchSource("cosplaytele", search_page("cosplaytele", "Miku", has_next_page=True)),
            StubSearchSource("hentaicosplay", SourceError("upstream unavailable")),
        ]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "miku", "source": "all"})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    assert payload["has_next_page"] is True
    assert [item["title"] for item in payload["items"]] == ["Miku"]
    assert payload["successful_sources"] == ["cosplaytele"]
    assert payload["errors"] == [
        {
            "source": "hentaicosplay",
            "code": "upstream_error",
            "retryable": False,
            "message": "upstream unavailable",
        }
    ]


@pytest.mark.anyio
async def test_search_associates_parallel_task_results_with_their_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = StubSearchRegistry(
        [
            StubSearchSource("cosplaytele", search_page("cosplaytele", "Fast")),
            StubSearchSource("hentaicosplay", search_page("hentaicosplay", "Slow"), delay=0.01),
        ]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "miku", "source": "all"})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    assert [item["title"] for item in payload["items"]] == ["Fast", "Slow"]
    assert payload["successful_sources"] == ["cosplaytele", "hentaicosplay"]


@pytest.mark.anyio
async def test_search_preserves_source_specific_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    source = StubSearchSource("cosplaytele", search_page("cosplaytele", "Miku"))
    source.category_names = ("cosplay",)
    seen: dict[str, str | None] = {}
    original_search = source.search

    async def record_category(
        query: str, page: int, category: str | None, *, exclude_ai: bool
    ) -> ListingPage:
        seen["category"] = category
        return await original_search(query, page, category, exclude_ai=exclude_ai)

    source.search = record_category  # type: ignore[method-assign]
    registry = StubSearchRegistry([source])
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool(
            "search", {"query": "miku", "source": "cosplaytele", "category": "byoru"}
        )
    assert result.is_error is not True
    assert seen["category"] == "byoru"


@pytest.mark.anyio
async def test_search_returns_an_error_when_every_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = StubSearchRegistry([StubSearchSource("cosplaytele", SourceError("unavailable"))])
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "miku", "source": "all"})
    assert result.is_error is True
    assert "All selected sources failed" in result.content[0].text


@pytest.mark.anyio
async def test_search_keeps_completed_results_when_total_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = StubSearchRegistry(
        [
            StubSearchSource("cosplaytele", search_page("cosplaytele", "Miku")),
            StubSearchSource("hentaicosplay", search_page("hentaicosplay", "Slow"), delay=1),
        ]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    monkeypatch.setattr("cosplaytele_mcp.server.SEARCH_TOTAL_TIMEOUT", 0.01)
    async with Client(mcp, raise_exceptions=True) as connected:
        result = await connected.call_tool("search", {"query": "miku", "source": "all"})
    assert result.is_error is not True
    payload = result.structured_content
    assert payload is not None
    assert [item["title"] for item in payload["items"]] == ["Miku"]
    assert payload["errors"][0]["code"] == "timeout"
    assert "timed out" in payload["errors"][0]["message"]


@pytest.mark.anyio
async def test_gallery_resource_can_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = StubSearchRegistry(
        [StubSearchSource("cosplaytele", search_page("cosplaytele", "Miku"))]
    )
    monkeypatch.setattr("cosplaytele_mcp.server.SourceRegistry", lambda http: registry)
    async with Client(mcp, raise_exceptions=True) as connected:
        resource = await connected.read_resource("gallery://cosplaytele/probe/")
    assert resource.contents[0].text is not None
    assert "Probe" in resource.contents[0].text


@pytest.mark.anyio
async def test_browse_latest_unsupported(client: Client) -> None:
    result = await client.call_tool("browse", {"source": "foamgirl", "sort": "latest"})
    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)
    assert "does not support latest" in result.content[0].text


@pytest.mark.anyio
async def test_browse_popular_unsupported(client: Client) -> None:
    result = await client.call_tool("browse", {"source": "everia", "sort": "popular"})
    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)
    assert "does not support popular" in result.content[0].text


@pytest.mark.anyio
async def test_find_gallery_prompt(client: Client) -> None:
    result = await client.get_prompt("find_gallery", {"query": "Kisaki"})
    text = result.messages[0].content.text
    assert "Kisaki" in text
    assert "search" in text
    assert "all" in text


@pytest.mark.anyio
async def test_source_completions(client: Client) -> None:
    prompt = await client.complete(
        ref=PromptReference(type="ref/prompt", name="find_gallery"),
        argument={"name": "source", "value": "cos"},
    )
    assert prompt.completion.values == ["cosplaytele"]
    resource = await client.complete(
        ref=ResourceTemplateReference(type="ref/resource", uri="gallery://{source}/{+path}"),
        argument={"name": "source", "value": "foam"},
    )
    assert resource.completion.values == ["foamgirl"]


@pytest.mark.anyio
async def test_get_gallery_image_window_schema(client: Client) -> None:
    tools = await client.list_tools()
    by_name = {tool.name: tool for tool in tools.tools}
    properties = by_name["get_gallery"].input_schema["properties"]
    assert properties["offset"]["default"] == 0
    assert properties["limit"]["default"] == 20
    assert properties["limit"]["maximum"] == 100
    open_props = by_name["open_url"].input_schema["properties"]
    assert "url" in open_props
    assert "source" not in open_props


def test_main_help(capsys: pytest.CaptureFixture[str]) -> None:
    from cosplaytele_mcp.server import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--transport" in output
    assert "streamable-http" in output


def test_main_configures_streamable_http_origin_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    from cosplaytele_mcp import server

    captured: dict[str, object] = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: captured.update(kwargs))
    server.main(
        [
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--allowed-origin",
            "https://mcp.example.com",
        ]
    )
    settings = captured["transport_security"]
    assert settings.allowed_hosts == ["0.0.0.0:8080"]
    assert settings.allowed_origins == ["https://mcp.example.com"]


def test_package_version() -> None:
    from pathlib import Path

    from cosplaytele_mcp.server import _package_version
    from cosplaytele_mcp.version import __version__

    assert __version__ == "0.4.0"
    assert _package_version() == __version__
    assert f'version = "{__version__}"' in Path("pyproject.toml").read_text()
