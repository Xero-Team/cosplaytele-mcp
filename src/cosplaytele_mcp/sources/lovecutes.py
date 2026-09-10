from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from selectolax.parser import HTMLParser, Node

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

TYPE_ID = 6
PAGE_SIZE = 16
ARTICLE_PAGE_SIZE = 10
PAGINATION_RE = re.compile(r"paginationData\s*=\s*(\{.*?\})\s*;", re.DOTALL)
COUNT_RE = re.compile(r"(?<!\d)(\d+)\s*P(?:\d+V)?\b", re.IGNORECASE)
FILE_COUNT_RE = re.compile(r"(?:文件数量|file count)\D+(\d+)", re.IGNORECASE)
VIDEO_RE = re.compile(r"\d+\s*V\b", re.IGNORECASE)


class LoveCutesSource(GallerySource):
    id = "lovecutes"
    name = "LoveCutes"
    base_url = "https://www.lovecutes.com"
    supports_latest = False
    popular_kind = "archive"
    category_names = ("cosplay",)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("LoveCutes only supports the cosplay category (type/6).")
        suffix = f"/type/{TYPE_ID}/page/{page}/" if page > 1 else f"/type/{TYPE_ID}/"
        return await self._listing(f"{self.base_url}{suffix}", page, require_cosplay=True)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("LoveCutes only supports the cosplay category (type/6).")
        if not query.strip():
            return await self.popular(page, category)
        suffix = f"/search/page/{page}/" if page > 1 else "/search/"
        url = f"{self.base_url}{suffix}?{urlencode({'s': query.strip()})}"
        # The site's search is global; keep only entries explicitly classified as type/6.
        return await self._listing(url, page, require_cosplay=True)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(first.css_first("h1.focusbox-title, h1"))
        if not title:
            raise SourceError(f"LoveCutes title missing: {url}")
        tags = [text_of(node) for node in first.css(".article-tags a") if text_of(node)]
        html = first.html or ""
        has_video = (
            bool(first.css_first(".image-container .play-icon"))
            or VIDEO_RE.search(title) is not None
        )
        total = self._known_count(title, html)
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
                total=total,
                has_more=True,
                tags=tags,
                has_video=has_video,
            )

        images = self._page_images(first)
        max_page = self._total_pages(html)
        stopped = False
        for page in range(2, max_page + 1):
            if budget is not None and len(images) >= budget:
                stopped = True
                break
            payload = await self.http.get_json(
                f"{url.rstrip('/')}/page/{page}/",
                referer=url,
                params={"ajax": "1"},
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("html"), str):
                raise SourceError(f"LoveCutes article pagination returned unexpected JSON: {url}")
            images.extend(self._page_images(HTMLParser(payload["html"])))
            pagination = payload.get("pagination")
            if isinstance(pagination, dict):
                max_page = int(pagination.get("total_pages") or max_page)
        images = list(dict.fromkeys(images))
        if not images:
            raise SourceError(f"LoveCutes gallery has no images: {url}")
        complete = not stopped and len(images) >= (total or len(images))
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            complete=complete,
            total=total,
            has_more=stopped or not complete,
            tags=tags,
            has_video=has_video,
        )

    async def _listing(self, url: str, page: int, *, require_cosplay: bool) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for node in document.css("article.excerpt"):
            item = self._listing_item(node, require_cosplay=require_cosplay)
            if item is not None:
                items.append(item)
        has_next = document.css_first("link[rel='next'], a.next-page a[data-page]") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def _listing_item(self, node: Node, *, require_cosplay: bool) -> ListingItem | None:
        category = node.css_first("a.imgbox-a[href*='/type/6/']")
        if require_cosplay and category is None:
            return None
        link = node.css_first("a.imgbox-link[href*='/article/']") or node.css_first(
            "h2 a[href*='/article/']"
        )
        href = abs_url(self.base_url, attr(link, "href"))
        title = attr(link, "title") or text_of(node.css_first("h2 a")) or text_of(link)
        if not href or not title:
            return None
        image = node.css_first("img.imgbox-img, img")
        html = node.html or ""
        date = text_of(node.css_first("footer time")) or None
        return ListingItem(
            title=title,
            path=path_of(href),
            url=href,
            thumbnail_url=img_src(image, self.base_url),
            published_at=date,
            image_count=self._known_count(title, html),
            has_video=bool(node.css_first(".play-icon")) or VIDEO_RE.search(title) is not None,
        )

    def _page_images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for node in document.css(
            ".image-container img.item-image__img, .article-content img, img.item-image__img"
        ):
            src = img_src(node, self.base_url)
            if src and src not in seen:
                seen.add(src)
                urls.append(src)
        return urls

    def _total_pages(self, html: str) -> int:
        match = PAGINATION_RE.search(html)
        if match:
            try:
                data = json.loads(match.group(1))
                return max(int(data.get("total_pages") or 1), 1)
            except TypeError, ValueError, json.JSONDecodeError:
                pass
        pages = [int(value) for value in re.findall(r'data-page=["\'](\d+)', html)]
        return max(pages, default=1)

    def _known_count(self, title: str, html: str) -> int | None:
        match = FILE_COUNT_RE.search(html)
        if match:
            return int(match.group(1))
        match = COUNT_RE.search(title)
        return int(match.group(1)) if match else None
