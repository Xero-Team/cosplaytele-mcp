from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    DEFAULT_IMAGE_LIMIT,
    MAX_IMAGE_LIMIT,
    BrowseSort,
    Gallery,
    ListingItem,
    ListingPage,
    SearchHit,
    SearchPage,
    SourceId,
    SourceInfo,
)
from cosplaytele_mcp.sources import SOURCE_TYPES, SourceError, SourceRegistry
from cosplaytele_mcp.sources.base import GallerySource
from cosplaytele_mcp.version import __version__

logger = logging.getLogger(__name__)

SEARCH_CONCURRENCY = 6
SOURCE_IDS: tuple[str, ...] = tuple(cls.id for cls in SOURCE_TYPES)
SOURCE_CHOICES = (*SOURCE_IDS, "all")

INSTRUCTIONS = """\
Browse cosplay gallery sites. Return image URLs only; never download binaries.

Typical flow:
1. list_sources — source ids, category slugs, search/latest/popular support
2. If the user pastes a post URL, call open_url(url). Do not guess source.
3. search(query) across all sources, browse(source, sort) for rankings,
   or browse_tag(source, tag) after you have a tag
4. get_gallery(source, path) using a hit's path. Default returns the first
   images plus image_count; pass offset/limit for more. limit=0 is metadata only.

exclude_ai defaults to true. get_gallery still returns AI sets, marked is_ai.
Prefer one source when the user names it; otherwise search all.
Do not fetch or decrypt video streams. has_video means the post has a video;
tell the user to open the gallery URL. download_urls are offsite zip/cloud links.
"""

READONLY_CLOSED = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READONLY_OPEN = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _package_version() -> str:
    return __version__


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
            supports_popular=cls.supports_popular,
            supports_latest=cls.supports_latest,
            supports_search=cls.supports_search,
            categories=list(cls.category_names),
        )
        for cls in SOURCE_TYPES
    ]


def interleave_hits(groups: list[list[SearchHit]]) -> list[SearchHit]:
    items: list[SearchHit] = []
    seen: set[tuple[str, str]] = set()
    index = 0
    while True:
        added = False
        for group in groups:
            if index >= len(group):
                continue
            hit = group[index]
            added = True
            key = (hit.source, hit.path)
            if key in seen:
                continue
            seen.add(key)
            items.append(hit)
        if not added:
            return items
        index += 1


def _hit_from_item(source: SourceId, item: ListingItem) -> SearchHit:
    return SearchHit(source=source, **item.model_dump())


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
    period: Annotated[
        str | None,
        Field(
            description=(
                "Optional ranking window. CosplayTele popular: last24hours, last7days, "
                "last30days, all. Hentai Cosplay popular: day, week, month, year."
            ),
        ),
    ] = None,
    exclude_ai: Annotated[bool, Field(description="Drop AI Art / AI Generated listings.")] = True,
) -> ListingPage:
    """List popular or latest galleries from one source.

    Use search() when the user names a character, series, model, or tag.
    If query is set, this tool searches that one source instead of ranking.
    If only category is set, list that category with sort (not a keyword search).
    Pass each item's path to get_gallery to fetch image URLs.
    """
    site = _registry(ctx).get(source)
    query = _blank_to_none(query)
    category = _blank_to_none(category)
    period = _blank_to_none(period)
    try:
        if query:
            page_result = await site.search(query, page, category, exclude_ai=exclude_ai)
        elif sort == "latest":
            if not site.supports_latest:
                raise ToolError(
                    f"{site.name} ({source}) does not support latest listings. "
                    "Use sort='popular' or search() instead."
                )
            page_result = await site.latest(page, category)
        else:
            if not site.supports_popular:
                raise ToolError(
                    f"{site.name} ({source}) does not support popular listings. "
                    "Use sort='latest' or search() instead."
                )
            page_result = await site.popular(page, category, period=period)
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
    Results are interleaved by source so one site cannot bury the rest.
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

    async def run(site: GallerySource) -> tuple[GallerySource, ListingPage | Exception]:
        try:
            async with semaphore:
                return site, await _search_site(site, query, page, category, exclude_ai)
        except Exception as exc:
            return site, exc

    tasks = [asyncio.create_task(run(site)) for site in sites]
    grouped: dict[str, list[SearchHit]] = {site.id: [] for site in sites}
    errors: list[str] = []
    has_next = False
    finished = 0
    try:
        for fut in asyncio.as_completed(tasks):
            site, outcome = await fut
            finished += 1
            await ctx.report_progress(finished, total=len(tasks), message=site.id)
            if isinstance(outcome, Exception):
                if source != "all":
                    raise _as_tool_error(outcome) from outcome
                detail = (
                    str(outcome) if isinstance(outcome, SourceError) else describe_error(outcome)
                )
                logger.warning("search failed for %s: %s", site.id, detail)
                errors.append(f"{site.id}: {detail}")
                continue
            result = outcome
            has_next = has_next or result.has_next_page
            grouped[result.source] = [
                _hit_from_item(result.source, item)
                for item in apply_ai_filter(result, exclude_ai).items
            ]
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    items = interleave_hits([grouped[site.id] for site in sites])
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
    title="Browse galleries by tag",
    annotations=READONLY_OPEN,
)
async def browse_tag(
    source: Annotated[SourceId, Field(description="Source id from list_sources.")],
    tag: Annotated[str, Field(min_length=1, max_length=200, description="Tag name or slug.")],
    ctx: Context[AppContext],
    page: Annotated[int, Field(ge=1, le=100, description="1-based page index.")] = 1,
    exclude_ai: Annotated[bool, Field(description="Drop AI Art / AI Generated listings.")] = True,
) -> ListingPage:
    """List galleries for one tag on one source.

    Prefer this over search() when you already have a tag from get_gallery.
    """
    label = tag.strip()
    if not label:
        raise ToolError("tag must not be blank.")
    site = _registry(ctx).get(source)
    try:
        return apply_ai_filter(await site.by_tag(label, page, exclude_ai=exclude_ai), exclude_ai)
    except (SourceError, httpx.HTTPError) as exc:
        raise _as_tool_error(exc) from exc


@mcp.tool(
    title="Find related galleries",
    annotations=READONLY_OPEN,
)
async def related(
    source: Annotated[SourceId, Field(description="Source id from list_sources.")],
    path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="ListingItem.path, SearchHit.path, or a full post URL.",
        ),
    ],
    ctx: Context[AppContext],
    page: Annotated[int, Field(ge=1, le=100, description="1-based page index.")] = 1,
    exclude_ai: Annotated[bool, Field(description="Drop AI Art / AI Generated listings.")] = True,
) -> ListingPage:
    """Find more galleries sharing a tag with the given set.

    Fetches metadata only, then browses the first tag. The original set is omitted.
    """
    site = _registry(ctx).get(source)
    try:
        return apply_ai_filter(
            await site.related(path.strip(), page, exclude_ai=exclude_ai), exclude_ai
        )
    except ToolError:
        raise
    except (SourceError, httpx.HTTPError) as exc:
        raise _as_tool_error(exc) from exc


@mcp.tool(
    title="Open a gallery URL",
    annotations=READONLY_OPEN,
)
async def open_url(
    url: Annotated[
        str,
        Field(
            min_length=8,
            max_length=500,
            description="Full post URL, e.g. https://cosplaytele.com/ryuuge-kisaki-4/.",
        ),
    ],
    ctx: Context[AppContext],
    offset: Annotated[int, Field(ge=0, description="Image window start.")] = 0,
    limit: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_IMAGE_LIMIT,
            description="Max image URLs to return. 0 returns metadata only.",
        ),
    ] = DEFAULT_IMAGE_LIMIT,
) -> Gallery:
    """Open a gallery from a full post URL.

    Picks the source from the URL host. Use this when the user pastes a link.
    """
    text = url.strip()
    if not text.startswith("http://") and not text.startswith("https://"):
        raise ToolError("url must be a full http(s) post URL.")
    registry = _registry(ctx)
    try:
        site = registry.by_url(text)
        return _flag_gallery(await site.gallery(text, offset=offset, limit=limit))
    except (SourceError, httpx.HTTPError) as exc:
        raise _as_tool_error(exc) from exc


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
    offset: Annotated[int, Field(ge=0, description="Image window start.")] = 0,
    limit: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_IMAGE_LIMIT,
            description="Max image URLs to return. 0 returns metadata only. Default 20.",
        ),
    ] = DEFAULT_IMAGE_LIMIT,
) -> Gallery:
    """Fetch title, tags, and a window of image URLs for one gallery.

    path is ListingItem.path / SearchHit.path, a relative path, or a full post URL.
    Does not download image files. AI galleries are returned with is_ai=true.
    If the user gave a full URL and you do not know the source, call open_url instead.
    """
    site = _registry(ctx).get(source)
    try:
        return _flag_gallery(await site.gallery(path.strip(), offset=offset, limit=limit))
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
        return _flag_gallery(
            await _registry(ctx)
            .get(cast(SourceId, source))
            .gallery(path, offset=0, limit=DEFAULT_IMAGE_LIMIT)
        )
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
        f"If the user gave a full post URL, call open_url with that URL. "
        f"Otherwise call search with query={query!r} and source={source!r}. "
        f"Pick the best hit, then call get_gallery with that source and path. "
        f"Summarize title, tags, image_count, and the returned image URLs. "
        f"Use offset/limit if you need more images. Do not download image binaries."
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


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="cosplaytele-mcp")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
        help="MCP transport. stdio for local hosts; streamable-http for remote deploy.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transports.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port for HTTP transports.")
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
