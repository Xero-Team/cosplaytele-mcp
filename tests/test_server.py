import pytest
from mcp import Client
from mcp.types import PromptReference, ResourceTemplateReference, TextContent

from cosplaytele_mcp.models import ListingItem, ListingPage, SourceId
from cosplaytele_mcp.server import mcp
from cosplaytele_mcp.sources import SourceError


class StubSearchSource:
    supports_search = True
    category_names: tuple[str, ...] = ()

    def __init__(self, source_id: SourceId, outcome: ListingPage | Exception) -> None:
        self.id = source_id
        self.outcome = outcome

    async def search(
        self, query: str, page: int, category: str | None, *, exclude_ai: bool
    ) -> ListingPage:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


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
    } <= tool_names
    by_name = {tool.name: tool for tool in tools.tools}
    assert by_name["list_sources"].annotations is not None
    assert by_name["list_sources"].annotations.read_only_hint is True
    assert by_name["list_sources"].annotations.open_world_hint is False
    assert by_name["search"].annotations is not None
    assert by_name["search"].annotations.read_only_hint is True
    assert by_name["search"].annotations.open_world_hint is True
    assert by_name["search"].annotations.idempotent_hint is None
    resources = await client.list_resources()
    uris = {str(resource.uri) for resource in resources.resources}
    assert "sources://catalog" in uris
    templates = await client.list_resource_templates()
    assert any("gallery://" in template.uri_template for template in templates.resource_templates)


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
    assert payload["errors"] == ["hentaicosplay: upstream unavailable"]


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


def test_package_version() -> None:
    from pathlib import Path

    from cosplaytele_mcp.server import _package_version
    from cosplaytele_mcp.version import __version__

    assert __version__ == "0.2.0"
    assert _package_version() == __version__
    assert f'version = "{__version__}"' in Path("pyproject.toml").read_text()
