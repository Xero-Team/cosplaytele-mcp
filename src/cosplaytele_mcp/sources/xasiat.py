from __future__ import annotations

import re
from typing import ClassVar

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

ITEMS_PER_PAGE = 12
COSPLAY_PATH = "albums/categories/cosplay"
PHOTOS_RE = re.compile(r"(\d+)\s*photos?", re.I)
VIDEO_RE = re.compile(r"\d+\s*V\b", re.I)
XHR_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class XasiatSource(GallerySource):
    id = "xasiat"
    name = "Xasiat"
    base_url = "https://www.xasiat.com"
    category_names = ("cosplay",)
    image_request_headers: ClassVar[dict[str, str]] = {"Referer": f"{base_url}/"}

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        return await self._albums(
            page,
            path=self._list_path(category),
            block_id="list_albums_common_albums_list",
            extra={"sort_by": "album_viewed_week"},
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._albums(
            page,
            path=self._list_path(category),
            block_id="list_albums_common_albums_list",
            extra={"sort_by": "post_date"},
        )

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        restrict_cosplay = self._require_search_category(category) is not None
        if not query.strip():
            return await self.latest(page, category)
        page_result = await self._albums(
            page,
            path="search/search",
            block_id="list_albums_albums_list_search_result",
            extra={"q": query.strip()},
        )
        if not restrict_cosplay:
            return page_result
        items = [item for item in page_result.items if looks_like_cosplay(item.title, item.path)]
        return page_result.model_copy(update={"items": items})

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = tag.strip()
        if not slug:
            raise SourceError("tag must not be blank")
        path = self._list_path(slug)
        return await self._albums(
            page,
            path=path,
            block_id="list_albums_common_albums_list",
            extra={},
        )

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = self._title(document)
        if not title:
            raise SourceError(f"Xasiat title missing: {url}")
        tags = [
            text_of(node)
            for node in document.css(".info-content a")
            if text_of(node) and "/albums/" in (attr(node, "href") or "")
        ]
        budget = needed_images(offset, limit)
        if budget == 0:
            return self.make_gallery(
                title=title,
                path=resolved,
                url=url,
                images=[],
                offset=offset,
                limit=limit,
                complete=False,
                has_more=True,
                tags=tags,
                has_video=VIDEO_RE.search(title) is not None,
            )
        images = self._page_images(document)
        if not images:
            raise SourceError(f"Xasiat gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            has_video=VIDEO_RE.search(title) is not None,
        )

    async def _albums(
        self,
        page: int,
        *,
        path: str,
        block_id: str,
        extra: dict[str, str],
    ) -> ListingPage:
        offset = ((page - 1) * ITEMS_PER_PAGE) + 1
        params = {
            "mode": "async",
            "function": "get_block",
            "block_id": block_id,
            "from": str(offset),
            **extra,
        }
        if "search" in block_id:
            params["from_albums"] = str(offset)
        url = f"{self.base_url}/{path.strip('/')}/"
        document = await self.http.get_html(
            url,
            referer=f"{self.base_url}/",
            params=params,
            headers=XHR_HEADERS,
        )
        items = self.listing_items(document)
        has_next = (
            any(
                "next" in text_of(node).lower()
                for node in document.css(".pagination a, .pages a, .pager a")
            )
            or len(items) >= ITEMS_PER_PAGE
        )
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def listing_items(self, document: HTMLParser) -> list[ListingItem]:
        items: list[ListingItem] = []
        seen: set[str] = set()
        for node in document.css(".list-albums .item a[href]"):
            href = abs_url(self.base_url, attr(node, "href"))
            if (
                not href
                or "/albums/" not in href
                or "/albums/categories/" in href
                or "/albums/tags/" in href
            ):
                continue
            path = path_of(href)
            if path in seen:
                continue
            title = attr(node, "title") or text_of(node.css_first(".title")) or text_of(node)
            if not title:
                continue
            seen.add(path)
            photos = text_of(node.css_first(".photos"))
            match = PHOTOS_RE.search(photos)
            items.append(
                ListingItem(
                    title=title,
                    path=path,
                    url=href,
                    thumbnail_url=img_src(node.css_first("img"), self.base_url),
                    image_count=int(match.group(1)) if match else None,
                    has_video=VIDEO_RE.search(title) is not None,
                )
            )
        return items

    def _page_images(self, document: HTMLParser) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for node in document.css("a[href*='/get_image/']"):
            href = abs_url(self.base_url, attr(node, "href"))
            if href and "/get_image/" in href and href not in seen:
                seen.add(href)
                images.append(href)
        return images

    def _title(self, document: HTMLParser) -> str:
        title = text_of(document.css_first(".entry-title, h1"))
        if not title:
            title = text_of(document.css_first("title"))
        return title.replace(" | Xasiat", "").strip()

    def _list_path(self, category: str | None) -> str:
        slug = (category or "cosplay").strip().lower().replace(" ", "-")
        if not slug:
            slug = "cosplay"
        if slug == "cosplay":
            return COSPLAY_PATH
        return f"albums/tags/{slug}"

    def _require_search_category(self, category: str | None) -> str | None:
        if not category:
            return None
        slug = category.strip().lower()
        if slug != "cosplay":
            raise SourceError(f"{self.name} search only supports the cosplay category")
        return slug


def looks_like_cosplay(title: str, path: str = "") -> bool:
    return "cosplay" in f"{title} {path}".lower()
