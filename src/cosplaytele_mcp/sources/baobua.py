from __future__ import annotations

import re
from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

TITLE_PREFIX_RE = re.compile(r"^BaoBua\.Net:\s*", re.I)
PAGE_SUFFIX_RE = re.compile(r"\s*\|\s*Page\s+\d+\s*/\s*\d+\s*$", re.I)
RELATED_TAGS_RE = re.compile(r"tag:\s*\[(.*?)\]", re.S)
TAG_ITEM_RE = re.compile(r'"([^"]+)"')


class BaoBuaSource(GallerySource):
    id = "baobua"
    name = "BaoBua"
    base_url = "https://baobua.net"
    supports_latest = False
    popular_kind = "archive"
    category_names = ("cosplay",)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("BaoBua default listings are locked to /category/Cosplay")
        url = f"{self.base_url}/category/Cosplay"
        if page > 1:
            url = f"{url}?page={page}"
        return await self._listing(url, page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("BaoBua default listings are locked to /category/Cosplay")
        if not query.strip():
            return await self.popular(page)
        return await self._listing(self._search_url(query, page), page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = self._title(first)
        if not title:
            raise SourceError(f"BaoBua title missing: {url}")
        tags = self._tags(first.html or "")
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
        images: list[str] = []
        seen: set[str] = set()
        page_url = url
        document = first
        visited: set[str] = set()
        stopped = False
        for _ in range(40):
            visited.add(page_url)
            for image in self._page_images(document):
                if image not in seen:
                    seen.add(image)
                    images.append(image)
            if budget is not None and len(images) >= budget:
                stopped = self._next_page(document, url) is not None
                break
            next_url = self._next_page(document, url)
            if not next_url or next_url in visited:
                break
            page_url = next_url
            document = await self.http.get_html(page_url, referer=url)
        else:
            stopped = self._next_page(document, url) is not None
        if not images:
            raise SourceError(f"BaoBua gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            complete=not stopped,
            has_more=stopped,
            tags=tags,
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items = self.listing_items(document)
        has_next = document.css_first("link[rel=next]") is not None or any(
            "next" in text_of(node).lower() for node in document.css("a.page-numbers")
        )
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def listing_items(self, document: HTMLParser) -> list[ListingItem]:
        items: list[ListingItem] = []
        seen: set[str] = set()
        for node in document.css(".thumb-view"):
            link = node.css_first('a[href*="/spot/"]')
            href = abs_url(self.base_url, attr(link, "href"))
            if not href:
                continue
            path = path_of(href)
            if path in seen:
                continue
            title = (
                attr(link, "title") or text_of(node.css_first("a.denomination")) or text_of(link)
            )
            if not title:
                continue
            seen.add(path)
            items.append(
                ListingItem(
                    title=title,
                    path=path,
                    url=href,
                    thumbnail_url=img_src(
                        node.css_first("img.xld, img:not(.play_img)"), self.base_url
                    ),
                )
            )
        return items

    def _page_images(self, document: HTMLParser) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for img in document.css("img"):
            src = img_src(img, self.base_url)
            if src and "/s0/" in src and "resize=" not in src and src not in seen:
                seen.add(src)
                images.append(src)
        return images

    def _next_page(self, document: HTMLParser, base: str) -> str | None:
        for node in document.css("a.page-numbers"):
            if "next" in text_of(node).lower():
                return abs_url(base, attr(node, "href"))
        return None

    def _search_url(self, query: str, page: int) -> str:
        params = {"s": query.strip()}
        if page > 1:
            params["page"] = str(page)
        return f"{self.base_url}/?{urlencode(params)}"

    def _title(self, document: HTMLParser) -> str:
        raw = text_of(document.css_first("title")) or text_of(document.css_first("h1"))
        raw = TITLE_PREFIX_RE.sub("", raw)
        return PAGE_SUFFIX_RE.sub("", raw).strip()

    def _tags(self, html: str) -> list[str]:
        match = RELATED_TAGS_RE.search(html)
        if not match:
            return []
        return [item for item in TAG_ITEM_RE.findall(match.group(1)) if item]
