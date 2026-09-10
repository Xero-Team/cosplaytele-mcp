from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceId = Literal[
    "cosplaytele",
    "hentaicosplay",
    "ososedki",
    "mitaku",
    "everia",
    "misskon",
    "fourkhd",
    "kiutaku",
    "cup2d",
    "beauty3600000",
    "foamgirl",
]

BrowseSort = Literal["popular", "latest"]
ListingKind = Literal["gallery", "directory"]

DEFAULT_IMAGE_LIMIT = 20
MAX_IMAGE_LIMIT = 100


class SourceInfo(BaseModel):
    id: SourceId
    name: str
    base_url: str
    supports_popular: bool = True
    supports_latest: bool
    supports_search: bool
    popular_kind: Literal["ranking", "archive", "featured"] = Field(
        default="ranking",
        description="Meaning of popular; archive is a category/default listing, not a measured rank.",
    )
    ranking_kinds: list[str] = Field(
        default_factory=list,
        description="Source-specific popular/ranking selectors, distinct from content categories.",
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Category slugs accepted by browse/search on this source.",
    )


class ListingItem(BaseModel):
    kind: ListingKind = Field(
        default="gallery",
        description="gallery paths can be opened with get_gallery; directory paths need another browse call.",
    )
    title: str
    path: str = Field(description="Source-relative path. Pass this to get_gallery.")
    url: str
    thumbnail_url: str | None = None
    is_ai: bool = False
    tags: list[str] = Field(default_factory=list)
    published_at: str | None = None
    image_count: int | None = None
    has_video: bool = False


class ListingPage(BaseModel):
    source: SourceId
    page: int
    has_next_page: bool
    items: list[ListingItem]


class SearchHit(ListingItem):
    source: SourceId


class SourceFailure(BaseModel):
    source: SourceId
    code: str = Field(description="Stable failure category, such as timeout or upstream_error.")
    retryable: bool
    message: str


class SearchPage(BaseModel):
    query: str
    page: int
    has_next_page: bool = Field(description="True if at least one source has another page.")
    items: list[SearchHit]
    successful_sources: list[SourceId] = Field(default_factory=list)
    errors: list[SourceFailure] = Field(
        default_factory=list,
        description="Per-source failures when searching all sources. Empty on a single-source call.",
    )


class ImageAsset(BaseModel):
    url: str = Field(description="Direct image URL for this gallery image.")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers required when fetching the direct image URL, such as Referer.",
    )


class Gallery(BaseModel):
    source: SourceId
    title: str
    path: str
    url: str
    thumbnail_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    published_at: str | None = None
    is_ai: bool = False
    has_video: bool = False
    download_urls: list[str] = Field(
        default_factory=list,
        description="Offsite zip/cloud links from the post, if any. Not video streams.",
    )
    image_count: int | None = Field(
        default=None,
        description="Total images if known. Null when the source was not fully scanned.",
    )
    image_offset: int = 0
    has_more_images: bool = False
    image_urls: list[str] = Field(
        description="Direct image URLs for this window. Do not download binaries."
    )
    image_assets: list[ImageAsset] = Field(
        default_factory=list,
        description="Image URLs with any source-required HTTP request headers.",
    )


def needed_images(offset: int, limit: int | None) -> int | None:
    if limit is None:
        return None
    if limit <= 0:
        return 0
    return max(offset, 0) + limit


def window_images(
    urls: list[str],
    *,
    offset: int = 0,
    limit: int | None = None,
    complete: bool = True,
    total: int | None = None,
    has_more: bool | None = None,
) -> tuple[list[str], int | None, int, bool]:
    offset = max(offset, 0)
    collected = len(urls)
    if complete or has_more is False:
        total = collected
    if limit is None:
        sliced = urls[offset:]
    elif limit <= 0:
        sliced = []
    else:
        sliced = urls[offset : offset + limit]
    end = offset + len(sliced)
    if total is not None:
        more = end < total
    elif has_more:
        more = True
    else:
        more = end < collected
    return sliced, total, offset, more
