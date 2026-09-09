from __future__ import annotations

from cosplaytele_mcp.htmlutil import path_of
from cosplaytele_mcp.models import Gallery, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import (
    fetch_wp_posts,
    fetch_wp_tag_id,
    images_from_html,
    listing_from_posts,
    wp_terms,
    wp_title,
)

THUMB_HOST = "img.4khd.com"
PAGE_HOST = "img.uuss.uk"


def _rewrite_cdn(url: str, thumbnail: bool) -> str:
    if "wp.com" not in url or "pic.4khd.com/" not in url:
        return url
    mapped = url.split("pic.4khd.com/", 1)[1]
    host = THUMB_HOST if thumbnail else PAGE_HOST
    return f"https://{host}/{mapped}"


class FourKHDSource(GallerySource):
    id = "fourkhd"
    name = "4KHD"
    base_url = "https://www.4khd.com"

    def _api(self) -> str:
        return f"{self.base_url}/index.php"

    async def popular(self, page: int, category: str | None = None) -> ListingPage:
        return await self._posts(
            page, extra={"rest_route": "/wp/v2/posts", "_embed": "1", "orderby": "modified"}
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._posts(
            page, extra={"rest_route": "/wp/v2/posts", "_embed": "1", "orderby": "date"}
        )

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        extra = {"rest_route": "/wp/v2/posts", "_embed": "1", "orderby": "date"}
        return await self._posts(page, search=query, extra=extra)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        tag_id = await fetch_wp_tag_id(
            self.http,
            self._api(),
            referer=f"{self.base_url}/",
            tag=tag,
            extra={"rest_route": "/wp/v2/tags"},
        )
        extra = {"rest_route": "/wp/v2/posts", "_embed": "1", "orderby": "date"}
        if tag_id is None:
            return await self._posts(page, search=tag, extra=extra)
        extra["tags"] = str(tag_id)
        return await self._posts(page, extra=extra)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        slug = resolved.rstrip("/").rsplit("/", 1)[-1]
        if slug.endswith(".html"):
            slug = slug[: -len(".html")]
        extra = {
            "rest_route": "/wp/v2/posts",
            "_embed": "1",
            "slug": slug,
            "per_page": "1",
            "page": "1",
        }
        posts, _ = await fetch_wp_posts(
            self.http,
            self._api(),
            page=1,
            referer=f"{self.base_url}/",
            extra=extra,
        )
        if not posts:
            raise SourceError(f"4KHD post not found: {resolved}")
        post = posts[0]
        title = wp_title(post)
        tags = wp_terms(post)
        link = str(post.get("link") or self.absolute(resolved))
        content = ""
        raw = post.get("content")
        if isinstance(raw, dict):
            content = str(raw.get("rendered") or "")
        images = [
            _rewrite_cdn(url, thumbnail=False) for url in images_from_html(content, self.base_url)
        ]
        images = list(dict.fromkeys(images))
        if not images:
            raise SourceError(f"4KHD gallery has no images: {resolved}")
        return self.make_gallery(
            title=title,
            path=path_of(link),
            url=link,
            images=images,
            offset=offset,
            limit=limit,
            thumbnail_url=_rewrite_cdn(images[0], thumbnail=True),
            tags=tags,
        )

    async def _posts(
        self, page: int, search: str = "", extra: dict[str, str] | None = None
    ) -> ListingPage:
        posts, has_next = await fetch_wp_posts(
            self.http,
            self._api(),
            page=page,
            referer=f"{self.base_url}/",
            search=search or None,
            extra=extra,
        )
        listing = listing_from_posts(self.id, page, posts, has_next)
        items = []
        for item in listing.items:
            thumb = _rewrite_cdn(item.thumbnail_url, thumbnail=True) if item.thumbnail_url else None
            items.append(item.model_copy(update={"thumbnail_url": thumb}))
        return listing.model_copy(update={"items": items})
