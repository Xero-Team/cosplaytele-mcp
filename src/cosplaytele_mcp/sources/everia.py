from __future__ import annotations

from cosplaytele_mcp.htmlutil import attr, text_of
from cosplaytele_mcp.models import Gallery, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import (
    fetch_wp_posts,
    fetch_wp_tag_id,
    images_from_html,
    listing_from_posts,
)

COSPLAY_CATEGORY = "7"


class EveriaSource(GallerySource):
    id = "everia"
    name = "Everia"
    base_url = "https://everia.club"
    category_names = ("cosplay",)

    def _posts_url(self) -> str:
        return f"{self.base_url}/wp-json/wp/v2/posts"

    async def popular(self, page: int, category: str | None = None) -> ListingPage:
        return await self._posts(
            page, extra={"categories": COSPLAY_CATEGORY, "orderby": "modified"}
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._posts(page, extra={"categories": COSPLAY_CATEGORY})

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        extra: dict[str, str] = {}
        if not query.strip():
            extra["categories"] = COSPLAY_CATEGORY
        return await self._posts(page, search=query, extra=extra)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        tag_id = await fetch_wp_tag_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/tags",
            referer=f"{self.base_url}/",
            tag=tag,
        )
        if tag_id is None:
            return await self.search(tag, page, None, exclude_ai=exclude_ai)
        return await self._posts(page, extra={"tags": str(tag_id)})

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first(".entry-title, h1"))
        if not title:
            raise SourceError(f"Everia title missing: {url}")
        tags = [text_of(node) for node in document.css(".post-tags > a") if text_of(node)]
        budget = needed_images(offset, limit)
        html = ""
        content = document.css_first(".entry-content")
        if content is not None:
            html = content.html or ""
        images = [] if budget == 0 else images_from_html(html, self.base_url)
        if budget != 0:
            page_links = [
                attr(node, "href")
                for node in document.css(".page-links a.post-page-numbers")
                if attr(node, "href")
            ]
            stopped = False
            for href in dict.fromkeys(page_links):
                if budget is not None and len(images) >= budget:
                    stopped = True
                    break
                extra = await self.http.get_html(href, referer=url)
                block = extra.css_first(".entry-content")
                images.extend(
                    images_from_html(
                        block.html if block is not None else extra.html or "", self.base_url
                    )
                )
            images = list(dict.fromkeys(images))
            if not images:
                raise SourceError(f"Everia gallery has no images: {url}")
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
