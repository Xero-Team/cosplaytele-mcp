import pytest
from mcp import Client
from mcp.types import PromptReference, ResourceTemplateReference, TextContent

from cosplaytele_mcp.server import mcp


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
    assert {"list_sources", "browse", "search", "get_gallery"} <= tool_names
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
async def test_browse_latest_unsupported(client: Client) -> None:
    result = await client.call_tool("browse", {"source": "foamgirl", "sort": "latest"})
    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)
    assert "does not support latest" in result.content[0].text


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
