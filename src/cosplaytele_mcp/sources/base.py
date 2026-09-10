from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Literal
from urllib.parse import urljoin

from cosplaytele_mcp.htmlutil import normalize_path
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import (
    Gallery,
    ImageAsset,
    ListingPage,
    SourceId,
    SourceInfo,
    window_images,
)


class SourceError(Exception):
    pass


class GallerySource(ABC):
    id: ClassVar[SourceId]
    name: ClassVar[str]
    base_url: ClassVar[str]
    supports_popular: ClassVar[bool] = True
    supports_latest: ClassVar[bool] = True
    supports_search: ClassVar[bool] = True
    popular_kind: ClassVar[Literal["ranking", "archive", "featured"]] = "ranking"
    ranking_kinds: ClassVar[tuple[str, ...]] = ()
    category_names: ClassVar[tuple[str, ...]] = ()
    image_request_headers: ClassVar[dict[str, str]] = {}

    def __init__(self, http: Http) -> None:
        self.http = http

    def info(self) -> SourceInfo:
        return SourceInfo(
            id=self.id,
            name=self.name,
            base_url=self.base_url,
            supports_popular=self.supports_popular,
            supports_latest=self.supports_latest,
            supports_search=self.supports_search,
            popular_kind=self.popular_kind,
            ranking_kinds=list(self.ranking_kinds),
            categories=list(self.category_names),
        )

    def absolute(self, path: str) -> str:
        resolved = self.resolve_path(path)
        return urljoin(f"{self.base_url}/", resolved.lstrip("/"))

    def resolve_path(self, path: str) -> str:
        try:
            return normalize_path(path, self.base_url)
        except ValueError as exc:
            raise SourceError(str(exc)) from exc

    def make_gallery(
        self,
        *,
        title: str,
        path: str,
        url: str,
        images: list[str],
        offset: int = 0,
        limit: int | None = None,
        complete: bool = True,
        total: int | None = None,
        has_more: bool | None = None,
        thumbnail_url: str | None = None,
        tags: list[str] | None = None,
        published_at: str | None = None,
        is_ai: bool = False,
        has_video: bool = False,
        download_urls: list[str] | None = None,
        image_headers: dict[str, str] | None = None,
    ) -> Gallery:
        sliced, count, off, more = window_images(
            images,
            offset=offset,
            limit=limit,
            complete=complete,
            total=total,
            has_more=has_more,
        )
        return Gallery(
            source=self.id,
            title=title,
            path=path,
            url=url,
            thumbnail_url=thumbnail_url or (images[0] if images else None),
            tags=tags or [],
            published_at=published_at,
            is_ai=is_ai,
            has_video=has_video,
            download_urls=download_urls or [],
            image_count=count,
            image_offset=off,
            has_more_images=more,
            image_urls=sliced,
            image_assets=[
                ImageAsset(url=image, headers=dict(image_headers or self.image_request_headers))
                for image in sliced
            ],
        )

    @abstractmethod
    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        raise NotImplementedError

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if not self.supports_latest:
            raise SourceError(
                f"{self.name} does not support latest listings. Use sort='popular' or search() instead."
            )
        raise NotImplementedError

    async def search(
        self,
        query: str,
        page: int,
        category: str | None,
        exclude_ai: bool = True,
    ) -> ListingPage:
        if not self.supports_search:
            raise SourceError(f"{self.name} does not support search")
        raise NotImplementedError

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        label = tag.strip()
        if not label:
            raise SourceError("tag must not be blank")
        return await self.search(label, page, None, exclude_ai=exclude_ai)

    async def related(self, path: str, page: int, exclude_ai: bool = True) -> ListingPage:
        gallery = await self.gallery(path.strip(), offset=0, limit=0)
        if not gallery.tags:
            raise SourceError(f"{gallery.title} has no tags to find related sets.")
        page_result = await self.by_tag(gallery.tags[0], page, exclude_ai=exclude_ai)
        items = [item for item in page_result.items if item.path != gallery.path]
        return page_result.model_copy(update={"items": items})

    @abstractmethod
    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        raise NotImplementedError
