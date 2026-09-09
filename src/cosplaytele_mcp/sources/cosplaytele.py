from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import quote

from selectolax.parser import HTMLParser

from cosplaytele_mcp.ai import looks_like_ai
from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, slugify, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import wp_image_count

TAG_HREF = re.compile(r"/(tag|category)/")
PAGE_SIZE = 20

CATEGORIES = {
    "all": "",
    "cosplay-nude": "category/cosplay-nude",
    "cosplay-ero": "category/cosplay-ero",
    "cosplay": "category/cosplay",
    "nude": "category/nude",
    "no-nude": "category/no-nude",
}


class CosplayTeleSource(GallerySource):
    id = "cosplaytele"
    name = "CosplayTele"
    base_url = "https://cosplaytele.com"
    category_names = tuple(CATEGORIES)

    async def popular(self, page: int, category: str | None = None) -> ListingPage:
        if category:
            return await self.search("", page, category, exclude_ai=False)
        offset = (page - 1) * PAGE_SIZE
        url = (
            f"{self.base_url}/wp-json/wordpress-popular-posts/v1/popular-posts"
            f"?offset={offset}&limit={PAGE_SIZE}&range=last7days"
            "&embed=true&_embed=wp:featuredmedia,wp:term"
            "&_fields=title,link,date,_embedded,_links.wp:featuredmedia"
        )
        payload = await self.http.get_json(url, referer=f"{self.base_url}/")
        if not isinstance(payload, list):
            raise SourceError("CosplayTele popular API returned unexpected JSON")
        items = [self._from_popular(entry) for entry in payload if isinstance(entry, dict)]
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=len(items) >= PAGE_SIZE,
            items=items,
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if category:
            return await self.search("", page, category, exclude_ai=False)
        suffix = f"/page/{page}/" if page > 1 else "/"
        return await self._parse_listing(f"{self.base_url}{suffix}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        params: dict[str, str] = {
            "per_page": str(PAGE_SIZE),
            "page": str(page),
            "_embed": "wp:featuredmedia,wp:term",
        }
        if query.strip():
            params["search"] = query.strip()
        if category:
            key = category.strip().lower()
            if key not in CATEGORIES:
                raise SourceError(
                    f"Unknown CosplayTele category {category!r}. Use one of: {', '.join(CATEGORIES)}"
                )
            slug = CATEGORIES[key].rsplit("/", 1)[-1]
            if slug:
                category_id = await self._category_id(slug)
                if category_id is None:
                    raise SourceError(f"CosplayTele category slug not found: {slug}")
                params["categories"] = str(category_id)
        if exclude_ai:
            ai_id = await self._category_id("ai-art")
            if ai_id is not None:
                params["categories_exclude"] = str(ai_id)
        if "search" not in params and "categories" not in params:
            return await self.latest(page)
        response = await self.http.get(
            f"{self.base_url}/wp-json/wp/v2/posts",
            referer=f"{self.base_url}/",
            params=params,
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise SourceError("CosplayTele search API returned unexpected JSON")
        items = [self._from_wp_post(entry) for entry in payload if isinstance(entry, dict)]
        total_pages = int(response.headers.get("x-wp-totalpages") or 0)
        has_next = page < total_pages if total_pages else len(items) >= PAGE_SIZE
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        encoded = quote(slug, safe="-")
        suffix = f"/page/{page}/" if page > 1 else "/"
        return await self._parse_listing(f"{self.base_url}/tag/{encoded}{suffix}", page)

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
        )

    async def _parse_listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for box in document.css("main div.box, div.box.box-blog-post"):
            link = box.css_first("h5 a, .box-text-inner a, h2 a, h3 a")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(link)
            if not href or not title:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(box.css_first("img"), self.base_url),
                )
            )
        has_next = document.css_first(".next.page-number, a.next.page-numbers") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    async def _category_id(self, slug: str) -> int | None:
        payload = await self.http.get_json(
            f"{self.base_url}/wp-json/wp/v2/categories",
            referer=f"{self.base_url}/",
            params={"slug": slug, "per_page": "1"},
        )
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            value = payload[0].get("id")
            return int(value) if value is not None else None
        return None

    def _from_wp_post(self, entry: dict[str, Any]) -> ListingItem:
        item = self._from_popular(entry)
        count = wp_image_count(entry, item.url)
        if count is None:
            return item
        return item.model_copy(update={"image_count": count})

    def _from_popular(self, entry: dict[str, Any]) -> ListingItem:
        title_obj = entry.get("title") or {}
        title = unescape(str(title_obj.get("rendered") or "")).strip()
        link = str(entry.get("link") or "")
        if not title or not link:
            raise SourceError("CosplayTele popular item missing title or link")
        media = (entry.get("_embedded") or {}).get("wp:featuredmedia") or []
        thumb = None
        if media and isinstance(media[0], dict):
            thumb = media[0].get("source_url")
        tags: list[str] = []
        for group in (entry.get("_embedded") or {}).get("wp:term") or []:
            if not isinstance(group, list):
                continue
            for term in group:
                if not isinstance(term, dict):
                    continue
                name = term.get("name") or term.get("slug")
                if name:
                    tags.append(str(name))
        tags = list(dict.fromkeys(tags))
        date = entry.get("date")
        published = str(date).split("T", 1)[0] if date else None
        return ListingItem(
            title=title,
            path=path_of(link),
            url=link,
            thumbnail_url=thumb,
            tags=tags,
            published_at=published,
            is_ai=looks_like_ai(title=title, path=path_of(link), tags=tags),
        )

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
            if not TAG_HREF.search(href):
                continue
            label = text_of(link)
            if label and label not in seen:
                seen.add(label)
                tags.append(label)
        return tags
