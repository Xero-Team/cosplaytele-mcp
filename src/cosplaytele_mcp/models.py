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


class SourceInfo(BaseModel):
    id: SourceId
    name: str
    base_url: str
    supports_latest: bool
    supports_search: bool
    categories: list[str] = Field(
        default_factory=list,
        description="Category slugs accepted by browse/search on this source.",
    )


class ListingItem(BaseModel):
    title: str
    path: str = Field(description="Source-relative path. Pass this to get_gallery.")
    url: str
    thumbnail_url: str | None = None
    is_ai: bool = False


class ListingPage(BaseModel):
    source: SourceId
    page: int
    has_next_page: bool
    items: list[ListingItem]


class SearchHit(BaseModel):
    source: SourceId
    title: str
    path: str = Field(description="Source-relative path. Pass this to get_gallery.")
    url: str
    thumbnail_url: str | None = None
    is_ai: bool = False


class SearchPage(BaseModel):
    query: str
    page: int
    has_next_page: bool = Field(description="True if at least one source has another page.")
    items: list[SearchHit]
    errors: list[str] = Field(
        default_factory=list,
        description="Per-source failures when searching all sources. Empty on a single-source call.",
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
    image_urls: list[str] = Field(description="Direct image URLs. Do not download binaries.")
