from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

PAGE_SUFFIX_RE = re.compile(r"\s*-\s*\(\s*Page\s+\d+\s*/\s*\d+\s*\)\s*$", re.I)


class KiutakuSource(GallerySource):
    id = "kiutaku"
    name = "Kiutaku"
    base_url = "https://kiutaku.com"
    listing_item_selector: ClassVar[str] = "div.blog > div.items-row"

    def _offset(self, page: int) -> int:
        return (page - 1) * 20

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        return await self._listing(f"{self.base_url}/hot?start={self._offset(page)}", page)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._listing(f"{self.base_url}/?start={self._offset(page)}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category:
            raise SourceError("Kiutaku does not support category filters")
        if not query.strip():
            return await self.latest(page)
        url = f"{self.base_url}/?{urlencode({'search': query.strip(), 'start': str(self._offset(page))})}"
        return await self._listing(url, page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = self._gallery_title(first)
        tags = [
            text_of(node).lstrip("#")
            for node in first.css("div.article-tags a.tag > span")
            if text_of(node)
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
            )
        page_urls = [url]
        for node in first.css("nav.pagination a"):
            href = abs_url(self.base_url, attr(node, "href"))
            if href:
                page_urls.append(href)
        images: list[str] = []
        seen_images: set[str] = set()
        stopped = False
        all_pages = list(dict.fromkeys(page_urls))
        unique_pages = all_pages[:40]
        truncated = len(all_pages) > len(unique_pages)
        for index, page_url in enumerate(unique_pages):
            if budget is not None and len(images) >= budget:
                stopped = True
                break
            document = first if page_url == url else await self.http.get_html(page_url, referer=url)
            for src in self._page_images(document):
                if src not in seen_images:
                    seen_images.add(src)
                    images.append(src)
            if budget is not None and len(images) >= budget and index + 1 < len(unique_pages):
                stopped = True
                break
        if not images:
            raise SourceError(f"{self.name} gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            complete=not stopped and not truncated,
            has_more=stopped or truncated,
            tags=tags,
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items = self.listing_items(document)
        next_link = document.css_first("a.pagination-next")
        has_next = False
        if next_link is not None:
            classes = (next_link.attributes.get("class") or "").split()
            has_next = "disabled" not in classes and next_link.attributes.get("disabled") is None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def listing_items(self, document: HTMLParser) -> list[ListingItem]:
        items: list[ListingItem] = []
        for node in document.css(self.listing_item_selector):
            link = node.css_first("a.item-link")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(node.css_first("h2")) or "Cosplay"
            if not href:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(node.css_first("img"), self.base_url),
                )
            )
        return items

    def _gallery_title(self, document: HTMLParser) -> str:
        return (
            PAGE_SUFFIX_RE.sub(
                "", text_of(document.css_first("div.article-header, h1")) or "Cosplay"
            ).strip()
            or "Cosplay"
        )

    def _page_images(self, document: HTMLParser) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for img in document.css("div.article-fulltext img[src], div.article-fulltext img"):
            src = img_src(img, self.base_url)
            if src and src not in seen:
                seen.add(src)
                images.append(src)
        return images
