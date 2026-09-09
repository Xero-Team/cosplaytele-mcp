from __future__ import annotations

from cosplaytele_mcp.htmlutil import download_urls_from_html, looks_like_video, text_of
from cosplaytele_mcp.models import Gallery, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import (
    fetch_wp_posts,
    fetch_wp_tag_id,
    fetch_wp_term_id,
    images_from_html,
    listing_from_posts,
)


class Cup2DSource(GallerySource):
    id = "cup2d"
    name = "Cup2D"
    base_url = "https://cup2d.com"
    supports_popular = False

    def _posts_url(self) -> str:
        return f"{self.base_url}/wp-json/wp/v2/posts"

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        raise SourceError(
            f"{self.name} does not support popular listings. Use sort='latest' or search() instead."
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._posts(page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category:
            raise SourceError("Cup2D does not support category filters")
        extra: dict[str, str] = {}
        if exclude_ai:
            await self._exclude_ai(extra)
        return await self._posts(page, search=query, extra=extra)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        tag_id = await fetch_wp_tag_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/tags",
            referer=f"{self.base_url}/",
            tag=tag,
        )
        extra: dict[str, str] = {}
        if exclude_ai:
            await self._exclude_ai(extra)
        if tag_id is None:
            return await self._posts(page, search=tag, extra=extra)
        extra["tags"] = str(tag_id)
        return await self._posts(page, extra=extra)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1.entry-title, .entry-title, h1"))
        if not title:
            raise SourceError(f"Cup2D title missing: {url}")
        tags = [
            text_of(node)
            for node in document.css("a[rel=tag], .post-tags a, .entry-tags a")
            if text_of(node)
        ]
        content = (
            document.css_first(".entry-content")
            or document.css_first(".post-content")
            or document.css_first("article")
        )
        html = content.html if content is not None else document.html or ""
        images = images_from_html(html, self.base_url)
        if not images:
            raise SourceError(f"Cup2D gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            has_video=looks_like_video(title=title, html=html),
            download_urls=download_urls_from_html(html),
        )

    async def _exclude_ai(self, extra: dict[str, str]) -> None:
        ids: list[str] = []
        for slug in ("ai-art", "aimodel"):
            term_id = await fetch_wp_term_id(
                self.http,
                f"{self.base_url}/wp-json/wp/v2/categories",
                referer=f"{self.base_url}/",
                term=slug,
            )
            if term_id is not None:
                ids.append(str(term_id))
        if ids:
            extra["categories_exclude"] = ",".join(ids)

    async def _posts(
        self, page: int, search: str = "", extra: dict[str, str] | None = None
    ) -> ListingPage:
        posts, has_next = await fetch_wp_posts(
            self.http,
            self._posts_url(),
            page=page,
            referer=f"{self.base_url}/",
            search=search or None,
            extra=extra,
        )
        return listing_from_posts(self.id, page, posts, has_next)
