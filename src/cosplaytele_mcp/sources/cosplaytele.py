from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from cosplaytele_mcp.ai import looks_like_ai
from cosplaytele_mcp.htmlutil import (
    abs_url,
    attr,
    download_urls_from_html,
    img_src,
    looks_like_video,
    path_of,
    slugify,
    text_of,
)
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError

TAG_HREF = re.compile(r"/tag/")
PAGE_SIZE = 20
POPULAR_PERIODS = ("last24hours", "last7days", "last30days", "all")
# The WordPress REST API that used to serve per-window rankings is broken
# upstream (HTTP 500), so popular falls back to the theme's curated
# "Popular Cosplay" widget.  Only these period pages still render statically.
POPULAR_PERIOD_PAGES = {
    "last24hours": "24-hours",
    "last7days": "7-day",
    "last30days": "7-day",
    "all": "7-day",
}
CATEGORIES = {
    "all": "",
    "cosplay-nude": "cosplay-nudee",
    "cosplay-ero": "cosplay-ero",
    "video-cosplay": "video-cosplayyy",
    "free-style": "free-style",
    "game": "game",
    "anime": "anime",
    "cosplay": "cosplay",
}


class CosplayTeleSource(GallerySource):
    id = "cosplaytele"
    name = "CosplayTele"
    base_url = "https://cosplaytele.com"
    category_names = tuple(CATEGORIES)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category.strip().lower() != "all":
            if period:
                raise SourceError(
                    "CosplayTele cannot combine a category listing with a popular period"
                )
            return await self.search("", page, category, exclude_ai=False)
        window = (period or "last7days").strip().lower()
        page_name = POPULAR_PERIOD_PAGES.get(window)
        if page_name is None:
            raise SourceError(
                f"Unknown CosplayTele popular period {period!r}. Use one of: {', '.join(POPULAR_PERIODS)}"
            )
        if page > 1:
            return ListingPage(source=self.id, page=page, has_next_page=False, items=[])
        document = await self.http.get_html(
            f"{self.base_url}/{page_name}/", referer=f"{self.base_url}/"
        )
        items = self._popular_items(document)
        if not items:
            raise SourceError(f"CosplayTele popular widget is missing from /{page_name}/")
        return ListingPage(source=self.id, page=page, has_next_page=False, items=items)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if category and category.strip().lower() != "all":
            return await self.search("", page, category, exclude_ai=False)
        return await self._listing(self._latest_url(page), page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        text = query.strip()
        category_slug = self._category_slug(category) if category else ""
        if not text and not category_slug:
            return await self.latest(page)
        if category_slug:
            return await self._listing(self._search_url(page, text, category_slug), page)
        return await self._listing(self._search_url(page, text), page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        encoded = quote(slug, safe="-")
        suffix = f"page/{page}/" if page > 1 else ""
        url = f"{self.base_url}/tag/{encoded}/{suffix}"
        try:
            return await self._listing(url, page)
        except SourceError, httpx.HTTPError:
            return await self.search(tag, page, None, exclude_ai=exclude_ai)

    async def related(self, path: str, page: int, exclude_ai: bool = True) -> ListingPage:
        resolved = self.resolve_path(path)
        document = await self.http.get_html(self.absolute(resolved), referer=f"{self.base_url}/")
        tag = self._first_tag(document)
        if not tag:
            raise SourceError(f"CosplayTele gallery has no tags: {resolved}")
        listing = await self.by_tag(tag, page, exclude_ai=exclude_ai)
        items = [item for item in listing.items if item.path != resolved]
        return listing.model_copy(update={"items": items})

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first(".entry-title, h1.entry-title, h1"))
        if not title:
            raise SourceError(f"CosplayTele gallery title missing: {url}")
        tags = self._tags(document)
        published = attr(document.css_first("time.updated, time.entry-date"), "datetime")
        images = self._gallery_images(document)
        if not images:
            raise SourceError(f"CosplayTele gallery has no images: {url}")
        html = document.html or ""
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            published_at=published.split("T", 1)[0] if published else None,
            is_ai=looks_like_ai(title=title, path=resolved, tags=tags),
            has_video=looks_like_video(title=title, html=html),
            download_urls=download_urls_from_html(html),
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=document.css_first("a.next.page-number") is not None,
            items=self._items(document),
        )

    def _latest_url(self, page: int) -> str:
        return f"{self.base_url}/" if page <= 1 else f"{self.base_url}/page/{page}/"

    def _search_url(self, page: int, query: str, category_slug: str = "") -> str:
        if category_slug:
            base = f"{self.base_url}/category/{category_slug}/"
            if page > 1:
                base = f"{base}page/{page}/"
            return f"{base}?s={quote(query)}" if query else base
        if page > 1:
            return f"{self.base_url}/page/{page}/?s={quote(query)}"
        return f"{self.base_url}/?s={quote(query)}"

    def _category_slug(self, category: str) -> str:
        key = category.strip().lower().strip("/")
        slug = CATEGORIES.get(key, key)
        if slug.startswith("category/"):
            slug = slug.rsplit("/", 1)[-1]
        return slug.strip("/")

    def _items(self, document: HTMLParser) -> list[ListingItem]:
        nodes = document.css("#post-list .col.post-item")
        if not nodes:
            nodes = document.css(".col.post-item")
        return self._collect(nodes)

    def _popular_items(self, document: HTMLParser) -> list[ListingItem]:
        footer = document.css_first(".footer-widgets")
        if footer is None:
            return []
        for row in footer.css("div[id^=row-]"):
            nodes = row.css(".col.post-item")
            if nodes:
                return self._collect(nodes)
        return []

    def _collect(self, nodes: list) -> list[ListingItem]:
        items: list[ListingItem] = []
        seen: set[str] = set()
        for node in nodes:
            link = node.css_first(".post-title a") or node.css_first(".box-image a")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(node.css_first(".post-title a")) or unescape(
                attr(link, "aria-label") or ""
            )
            if not href or not title:
                continue
            path = path_of(href)
            if path in seen:
                continue
            seen.add(path)
            tags = list(
                dict.fromkeys(
                    text_of(term) for term in node.css(".cat-label, .tag-label") if text_of(term)
                )
            )
            items.append(
                ListingItem(
                    title=title,
                    path=path,
                    url=href,
                    thumbnail_url=img_src(node.css_first("img.wp-post-image, img"), self.base_url),
                    tags=tags,
                    is_ai=looks_like_ai(title=title, path=path, tags=tags),
                    has_video=node.css_first(".icon-play") is not None
                    or looks_like_video(title=title),
                )
            )
        return items

    def _gallery_images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for img in document.css(".gallery-item img"):
            src = img_src(img, self.base_url)
            if src and src not in seen:
                seen.add(src)
                urls.append(src)
        if urls:
            return urls
        for img in document.css("figure img, .entry-content img, article img"):
            src = img_src(img, self.base_url)
            if src and "/wp-content/uploads/" in src and src not in seen:
                seen.add(src)
                urls.append(src)
        return urls

    def _tags(self, document: HTMLParser) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for link in document.css("#main a, .entry-content a, a[rel=tag]"):
            href = attr(link, "href") or ""
            if not re.search(r"/(tag|category)/", href):
                continue
            label = text_of(link)
            if label and label not in seen:
                seen.add(label)
                tags.append(label)
        return tags

    def _first_tag(self, document: HTMLParser) -> str | None:
        for link in document.css("#main a, .entry-content a, a[rel=tag]"):
            href = attr(link, "href") or ""
            if not TAG_HREF.search(href):
                continue
            label = text_of(link)
            if label:
                return label
        return None
