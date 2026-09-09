from __future__ import annotations

from cosplaytele_mcp.htmlutil import text_of
from cosplaytele_mcp.models import Gallery, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import fetch_wp_posts, images_from_html, listing_from_posts


class Cup2DSource(GallerySource):
    id = "cup2d"
    name = "Cup2D"
    base_url = "https://cup2d.com"

    def _posts_url(self) -> str:
        return f"{self.base_url}/wp-json/wp/v2/posts"

    async def popular(self, page: int) -> ListingPage:
        return await self._posts(page)

    async def latest(self, page: int) -> ListingPage:
        return await self._posts(page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        return await self._posts(page, search=query)

    async def gallery(self, path: str) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1.entry-title, .entry-title, h1"))
        if not title:
            raise SourceError(f"Cup2D title missing: {url}")
        content = document.css_first("article, .entry-content, .post-content")
        html = content.html if content is not None else document.html or ""
        images = images_from_html(html, self.base_url)
        if not images:
            raise SourceError(f"Cup2D gallery has no images: {url}")
        tags = [
            text_of(node)
            for node in document.css("a[rel=tag], .post-tags a, .entry-tags a")
            if text_of(node)
        ]
        return Gallery(
            source=self.id,
            title=title,
            path=resolved,
            url=url,
            thumbnail_url=images[0],
            tags=tags,
            image_urls=images,
        )

    async def _posts(self, page: int, search: str = "") -> ListingPage:
        posts, has_next = await fetch_wp_posts(
            self.http,
            self._posts_url(),
            page=page,
            referer=f"{self.base_url}/",
            search=search or None,
        )
        return listing_from_posts(self.id, page, posts, has_next)
