from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Literal, cast

import httpx
from mcp.server import CacheHint, MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
    ToolAnnotations,
)
from pydantic import Field

from cosplaytele_mcp.ai import apply_ai_filter, looks_like_ai
from cosplaytele_mcp.http import DEFAULT_HEADERS, HTTP_LIMITS, HTTP_TIMEOUT, Http, describe_error
from cosplaytele_mcp.models import (
    BrowseSort,
    Gallery,
    ListingPage,
    SearchHit,
    SearchPage,
    SourceId,
    SourceInfo,
)
from cosplaytele_mcp.sources import SOURCE_TYPES, SourceError, SourceRegistry
from cosplaytele_mcp.sources.base import GallerySource

logger = logging.getLogger(__name__)

SEARCH_CONCURRENCY = 6
SOURCE_IDS: tuple[str, ...] = tuple(cls.id for cls in SOURCE_TYPES)
SOURCE_CHOICES = (*SOURCE_IDS, "all")

INSTRUCTIONS = """\
Browse cosplay gallery sites. Return image URLs only; never download binaries.

Typical flow:
1. list_sources — source ids, category slugs, search/latest support
2. search(query) across all sources, or browse(source, sort) for rankings
3. get_gallery(source, path) using a hit's path (full post URLs also work)

exclude_ai defaults to true. get_gallery still returns AI sets, marked is_ai.
Prefer one source when the user names it; otherwise search all.
"""

READONLY_CLOSED = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READONLY_OPEN = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _package_version() -> str:
    try:
        return version("cosplaytele-mcp")
    except PackageNotFoundError:
        return "0.1.0"


@dataclass
class AppContext:
    http: Http
    sources: SourceRegistry


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
        limits=HTTP_LIMITS,
    ) as client:
        http = Http(client)
        yield AppContext(http=http, sources=SourceRegistry(http))


mcp = MCPServer(
    "cosplaytele",
    title="CosplayTele Gallery Browser",
    description="Browse cosplay gallery sites. Returns metadata and image URLs only.",
    instructions=INSTRUCTIONS,
    version=_package_version(),
    lifespan=app_lifespan,
    cache_hints={
        "tools/list": CacheHint(ttl_ms=3_600_000, scope="public"),
        "resources/list": CacheHint(ttl_ms=3_600_000, scope="public"),
        "prompts/list": CacheHint(ttl_ms=3_600_000, scope="public"),
        "resources/templates/list": CacheHint(ttl_ms=3_600_000, scope="public"),
    },
)


def _registry(ctx: Context[AppContext]) -> SourceRegistry:
    return ctx.request_context.lifespan_context.sources


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _as_tool_error(exc: BaseException) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, SourceError):
        return ToolError(str(exc) or describe_error(exc))
    if isinstance(exc, httpx.HTTPError):
        return ToolError(f"HTTP request failed: {describe_error(exc)}")
    return ToolError(f"Source request failed: {describe_error(exc)}")


def _flag_gallery(gallery: Gallery) -> Gallery:
    if gallery.is_ai:
        return gallery
    return gallery.model_copy(
        update={"is_ai": looks_like_ai(title=gallery.title, path=gallery.path, tags=gallery.tags)},
    )


def _source_catalog() -> list[SourceInfo]:
    return [
        SourceInfo(
            id=cls.id,
            name=cls.name,
            base_url=cls.base_url,
            supports_latest=cls.supports_latest,
            supports_search=cls.supports_search,
            categories=list(cls.category_names),
        )
        for cls in SOURCE_TYPES
    ]


@mcp.tool(
    title="List gallery sources",
    annotations=READONLY_CLOSED,
)
def list_sources() -> list[SourceInfo]:
    """Return every gallery source this server can query.

    Call this first when you need a source id, a category slug, or to check
    whether a source supports search or latest listings.
    """
    return _source_catalog()


@mcp.tool(
    title="Browse a gallery source",
    annotations=READONLY_OPEN,
)
async def browse(
    source: Annotated[SourceId, Field(description="Source id from list_sources.")],
    ctx: Context[AppContext],
    sort: Annotated[
        BrowseSort,
        Field(
            description="popular (rankings / hot) or latest (newest posts). Ignored when query is set."
        ),
    ] = "popular",
    page: Annotated[
        int, Field(ge=1, le=100, description="1-based page index. About 20 items per page.")
    ] = 1,
    query: Annotated[
        str | None,
        Field(
            max_length=200,
            description="If set, search this one source instead of listing rankings. Prefer search() for queries.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        Field(description="Optional category slug from list_sources.categories."),
    ] = None,
    exclude_ai: Annotated[bool, Field(description="Drop AI Art / AI Generated listings.")] = True,
) -> ListingPage:
    """List popular or latest galleries from one source.

    Use search() when the user names a character, series, model, or tag.
    If query is set, this tool searches that one source instead of ranking.
    Pass each item's path to get_gallery to fetch image URLs.
    """
    site = _registry(ctx).get(source)
    query = _blank_to_none(query)
    category = _blank_to_none(category)
    try:
        if query or category:
            page_result = await site.search(query or "", page, category, exclude_ai=exclude_ai)
        elif sort == "latest":
            if not site.supports_latest:
                raise ToolError(
                    f"{site.name} ({source}) does not support latest listings. "
                    "Use sort='popular' or search() instead."
                )
            page_result = await site.latest(page)
        else:
            page_result = await site.popular(page)
        return apply_ai_filter(page_result, exclude_ai)
    except ToolError:
        raise
    except (SourceError, httpx.HTTPError) as exc:
        raise _as_tool_error(exc) from exc


@mcp.tool(
    title="Search galleries",
    annotations=READONLY_OPEN,
)
async def search(
    query: Annotated[
        str,
        Field(min_length=1, max_length=200, description="Character, series, model, or tag."),
    ],
    ctx: Context[AppContext],
    source: Annotated[
        SourceId | Literal["all"],
        Field(
            description="One source id from list_sources, or all to query every searchable source in parallel."
        ),
    ] = "all",
    page: Annotated[int, Field(ge=1, le=100, description="1-based page index.")] = 1,
    category: Annotated[
        str | None,
        Field(description="Optional category slug. Applied only on sources that list it."),
    ] = None,
    exclude_ai: Annotated[bool, Field(description="Drop AI Art / AI Generated listings.")] = True,
) -> SearchPage:
    """Search galleries by character, series, model, or tag.

    source="all" (default) queries every searchable source in parallel.
    Failed sources appear in errors and do not fail the whole call.
    Pass a hit's source and path to get_gallery.
    """
    query = query.strip()
    if not query:
        raise ToolError("query must not be blank.")
    category = _blank_to_none(category)
    registry = _registry(ctx)
    sites = (
        [registry.get(source)]
        if source != "all"
        else [site for site in registry.all() if site.supports_search]
    )
    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def run(site: GallerySource) -> ListingPage:
        async with semaphore:
            return await _search_site(site, query, page, category, exclude_ai)

    tasks = {asyncio.create_task(run(site)): site for site in sites}
    items: list[SearchHit] = []
    errors: list[str] = []
    has_next = False
    finished = 0
    try:
        for fut in asyncio.as_completed(tasks):
            site = tasks[fut]
            finished += 1
            await ctx.report_progress(finished, total=len(tasks), message=site.id)
            try:
                result = await fut
            except Exception as exc:
                if source != "all":
                    raise _as_tool_error(exc) from exc
                detail = str(exc) if isinstance(exc, SourceError) else describe_error(exc)
                logger.warning("search failed for %s: %s", site.id, detail)
                errors.append(f"{site.id}: {detail}")
                continue
            has_next = has_next or result.has_next_page
            items.extend(
                SearchHit(
                    source=result.source,
                    title=item.title,
                    path=item.path,
                    url=item.url,
                    thumbnail_url=item.thumbnail_url,
                    is_ai=item.is_ai,
                )
                for item in apply_ai_filter(result, exclude_ai).items
            )
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    return SearchPage(query=query, page=page, has_next_page=has_next, items=items, errors=errors)


async def _search_site(
    site: GallerySource,
    query: str,
    page: int,
    category: str | None,
    exclude_ai: bool,
) -> ListingPage:
    site_category = category
    if category and site.category_names and category not in site.category_names:
        site_category = None
    try:
        return await site.search(query, page, site_category, exclude_ai=exclude_ai)
    except (SourceError, httpx.HTTPError):
        raise
    except Exception as exc:
        raise SourceError(describe_error(exc)) from exc


@mcp.tool(
    title="Get a gallery",
    annotations=READONLY_OPEN,
)
async def get_gallery(
    source: Annotated[SourceId, Field(description="Source id from list_sources.")],
    path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="ListingItem.path, SearchHit.path, a relative path, or a full post URL.",
        ),
    ],
    ctx: Context[AppContext],
) -> Gallery:
    """Fetch title, tags, and image URLs for one gallery.

    path is ListingItem.path / SearchHit.path, a relative path, or a full post URL.
    Does not download image files. AI galleries are returned with is_ai=true.
    """
    site = _registry(ctx).get(source)
    try:
        return _flag_gallery(await site.gallery(path.strip()))
    except (SourceError, httpx.HTTPError) as exc:
        raise _as_tool_error(exc) from exc


@mcp.resource("sources://catalog", mime_type="application/json", title="Gallery source catalog")
def sources_catalog() -> list[SourceInfo]:
    """Catalog of gallery sources."""
    return _source_catalog()


@mcp.resource("gallery://{source}/{+path}", mime_type="application/json", title="Gallery")
async def gallery_resource(source: str, path: str, ctx: Context[AppContext]) -> Gallery:
    """One gallery addressed as gallery://<source>/<path>."""
    if source not in SOURCE_IDS:
        raise ResourceNotFoundError(f"Unknown source {source!r}")
    try:
        return _flag_gallery(await _registry(ctx).get(cast(SourceId, source)).gallery(path))
    except SourceError as exc:
        raise ResourceNotFoundError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ResourceError(f"HTTP request failed: {describe_error(exc)}") from exc


@mcp.prompt(title="Find a gallery")
def find_gallery(
    query: Annotated[str, Field(description="Character, series, or model to search for.")],
    source: Annotated[
        SourceId | Literal["all"],
        Field(description="Source id from list_sources, or all. Default searches every source."),
    ] = "all",
) -> str:
    """Search a cosplay gallery source and open a matching set."""
    return (
        f"Call search with query={query!r} and source={source!r}. "
        f"Pick the best hit, then call get_gallery with that source and path. "
        f"Summarize title, tags, image count, and the first few image URLs. "
        f"Do not download image binaries."
    )


@mcp.completion()
async def handle_completion(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,
) -> Completion | None:
    prefix = argument.value.strip().lower()
    if argument.name == "source":
        if isinstance(ref, PromptReference) and ref.name == "find_gallery":
            values = [item for item in SOURCE_CHOICES if item.startswith(prefix)]
            return Completion(values=values)
        if isinstance(ref, ResourceTemplateReference) and "gallery://" in ref.uri:
            values = [item for item in SOURCE_IDS if item.startswith(prefix)]
            return Completion(values=values)
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
