from __future__ import annotations

from typing import ClassVar
from urllib.parse import quote, urlencode

from cosplaytele_mcp.models import ListingPage
from cosplaytele_mcp.sources.base import SourceError
from cosplaytele_mcp.sources.kiutaku import KiutakuSource

COSPLAY_TAG = "cosplay-10688"


class BuonDuaSource(KiutakuSource):
    id = "buondua"
    name = "Buon Dua"
    base_url = "https://buondua.com"
    supports_latest = False
    popular_kind = "archive"
    category_names = ("cosplay",)
    listing_item_selector: ClassVar[str] = "div.items-row"

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("Buon Dua default listings are locked to the cosplay tag")
        return await self._listing(self._tag_url(COSPLAY_TAG, page), page)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        raise SourceError(
            f"{self.name} does not support latest listings. Use sort='popular' or search() instead."
        )

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("Buon Dua default listings are locked to the cosplay tag")
        if not query.strip():
            return await self.popular(page)
        url = (
            f"{self.base_url}/?"
            f"{urlencode({'search': query.strip(), 'start': str(self._offset(page))})}"
        )
        return await self._listing(url, page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = tag.strip()
        if not slug:
            raise SourceError("tag must not be blank")
        if slug.lower() == "cosplay":
            slug = COSPLAY_TAG
        return await self._listing(self._tag_url(quote(slug, safe="-"), page), page)

    def _tag_url(self, tag: str, page: int) -> str:
        start = self._offset(page)
        if start:
            return f"{self.base_url}/tag/{tag}?start={start}"
        return f"{self.base_url}/tag/{tag}"
