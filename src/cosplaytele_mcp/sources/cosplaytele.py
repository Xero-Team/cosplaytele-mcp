from __future__ import annotations

import re
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser

from cosplaytele_mcp.ai import looks_like_ai
from cosplaytele_mcp.htmlutil import (
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
from cosplaytele_mcp.wordpress import (
    fetch_wp_posts,
    fetch_wp_tag_id,
    listing_from_posts,
    wp_has_video,
    wp_image_count,
)

TAG_HREF = re.compile(r"/(tag|category)/")
PAGE_SIZE = 20
POPULAR_PERIODS = ("last24hours", "last7days", "last30days", "all")
CATEGORIES = {
    "all": "",
    "cosplay-nude": "cosplay-nude",
    "cosplay-ero": "cosplay-ero",
    "video-cosplay": "video-cosplayy",
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
        if category:
            return await self.search("", page, category, exclude_ai=False)
        window = (period or "last7days").strip()
        if window not in POPULAR_PERIODS:
            raise SourceError(
                f"Unknown CosplayTele popular period {period!r}. Use one of: {', '.join(POPULAR_PERIODS)}"
            )
        offset = (page - 1) * PAGE_SIZE
        url = (
            f"{self.base_url}/wp-json/wordpress-popular-posts/v1/popular-posts"
            f"?offset={offset}&limit={PAGE_SIZE}&range={window}"
            "&embed=true&_embed=wp:featuredmedia,wp:term"
            "&_fields=title,link,date,content,_embedded,_links.wp:featuredmedia"
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
        return await self._wp_posts(page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        extra: dict[str, str] = {}
        if category:
            category_id = await self._resolve_category_id(category)
            if category_id is None:
                raise SourceError(f"CosplayTele category slug not found: {category}")
            extra["categories"] = str(category_id)
        if exclude_ai:
            await self._exclude_ai(extra)
        if not query.strip() and "categories" not in extra:
            return await self.latest(page)
        return await self._wp_posts(page, search=query, extra=extra)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        extra: dict[str, str] = {}
        tag_id = await fetch_wp_tag_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/tags",
            referer=f"{self.base_url}/",
            tag=slug,
        )
        if tag_id is not None:
            extra["tags"] = str(tag_id)
        else:
            category_id = await self._category_id(slug)
            if category_id is None:
                return await self.search(tag, page, None, exclude_ai=exclude_ai)
            extra["categories"] = str(category_id)
        if exclude_ai:
            await self._exclude_ai(extra)
        return await self._wp_posts(page, extra=extra)

    async def related(self, path: str, page: int, exclude_ai: bool = True) -> ListingPage:
        resolved = self.resolve_path(path)
        slug = resolved.strip("/").rsplit("/", 1)[-1]
        posts, _ = await fetch_wp_posts(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/posts",
            page=1,
            referer=f"{self.base_url}/",
            extra={"slug": slug, "per_page": "1", "page": "1"},
        )
        if not posts:
            return await super().related(path, page, exclude_ai=exclude_ai)
        post_id = posts[0].get("id")
        if post_id is None:
            return await super().related(path, page, exclude_ai=exclude_ai)
        payload = await self.http.get_json(
            f"{self.base_url}/wp-json/contextual-related-posts/v1/posts",
            referer=f"{self.base_url}/",
            params={"id": str(post_id), "limit": str(PAGE_SIZE * page)},
        )
        related_posts = (
            [entry for entry in payload if isinstance(entry, dict)]
            if isinstance(payload, list)
            else []
        )
        start = (page - 1) * PAGE_SIZE
        window = related_posts[start : start + PAGE_SIZE]
        listing = listing_from_posts(self.id, page, window, len(related_posts) > start + PAGE_SIZE)
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

    async def _wp_posts(
        self, page: int, search: str = "", extra: dict[str, str] | None = None
    ) -> ListingPage:
        posts, has_next = await fetch_wp_posts(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/posts",
            page=page,
            referer=f"{self.base_url}/",
            search=search or None,
            extra=extra,
        )
        listing = listing_from_posts(self.id, page, posts, has_next)
        items = [
            item.model_copy(
                update={"is_ai": looks_like_ai(title=item.title, path=item.path, tags=item.tags)}
            )
            for item in listing.items
        ]
        return listing.model_copy(update={"items": items})

    async def _resolve_category_id(self, category: str) -> int | None:
        key = category.strip().lower().strip("/")
        slug = CATEGORIES.get(key, key)
        if slug.startswith("category/"):
            slug = slug.rsplit("/", 1)[-1]
        if not slug:
            return None
        return await self._category_id(slug)

    async def _exclude_ai(self, extra: dict[str, str]) -> None:
        ai_id = await self._category_id("ai-art")
        if ai_id is not None:
            extra["categories_exclude"] = str(ai_id)

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
            has_video=wp_has_video(entry) or looks_like_video(title=title),
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
