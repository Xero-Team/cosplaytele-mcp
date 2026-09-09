from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar
from urllib.parse import urljoin

from cosplaytele_mcp.htmlutil import normalize_path
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import Gallery, ListingPage, SourceId, SourceInfo


class SourceError(Exception):
    pass


class GallerySource(ABC):
    id: ClassVar[SourceId]
    name: ClassVar[str]
    base_url: ClassVar[str]
    supports_latest: ClassVar[bool] = True
    supports_search: ClassVar[bool] = True
    category_names: ClassVar[tuple[str, ...]] = ()

    def __init__(self, http: Http) -> None:
        self.http = http

    def info(self) -> SourceInfo:
        return SourceInfo(
            id=self.id,
            name=self.name,
            base_url=self.base_url,
            supports_latest=self.supports_latest,
            supports_search=self.supports_search,
            categories=list(self.category_names),
        )

    def absolute(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    def resolve_path(self, path: str) -> str:
        try:
            return normalize_path(path, self.base_url)
        except ValueError as exc:
            raise SourceError(str(exc)) from exc

    @abstractmethod
    async def popular(self, page: int) -> ListingPage:
        raise NotImplementedError

    async def latest(self, page: int) -> ListingPage:
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

    @abstractmethod
    async def gallery(self, path: str) -> Gallery:
        raise NotImplementedError
