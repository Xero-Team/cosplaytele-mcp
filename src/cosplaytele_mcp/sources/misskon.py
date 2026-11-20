from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

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
from cosplaytele_mcp.wordpress import (
    fetch_wp_posts,
    fetch_wp_tag_id,
    fetch_wp_term_id,
    images_from_html,
    listing_from_posts,
    wp_content_html,
    wp_date,
    wp_terms,
    wp_title,
)

if TYPE_CHECKING:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

# Rendering a paginated post injects non-photo assets (a "Go to page 1" download
# banner, logos, lazy-load blanks). Keep them out of image_urls.
BANNER_MARKERS = ("/media/", "/logo", "dlp", "blank_", "data:")


def _is_banner(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in BANNER_MARKERS)


class MissKonSource(GallerySource):
    id = "misskon"
    name = "MissKon"
    base_url = "https://misskon.com"
    supports_popular = False
    category_names = ("cosplay",)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        raise SourceError(
            f"{self.name} does not support popular listings. Use sort='latest' or search() instead."
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if not category:
            return await self._wp_or_tag("cosplay", page)
        return await self._category_posts(category, page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        text = query.strip()
        if text:
            extra: dict[str, str] = {}
            if category:
                extra.update(await self._term_filter(category))
            if exclude_ai:
                await self._exclude_ai(extra)
            return await self._wp_posts(page, search=text, extra=extra)
        return await self.latest(page, category)

    async def _category_posts(self, category: str, page: int) -> ListingPage:
        category_id = await self._term_id("categories", category)
        if category_id is None:
            # "cosplay" (and friends) are registered as tags on MissKon now.
            return await self._wp_or_tag(category, page)
        return await self._wp_posts(page, extra={"categories": str(category_id)})

    async def _term_filter(self, term: str) -> dict[str, str]:
        category_id = await self._term_id("categories", term)
        if category_id is not None:
            return {"categories": str(category_id)}
        tag_id = await self._term_id("tags", term)
        return {"tags": str(tag_id)} if tag_id is not None else {}

    async def _term_id(self, taxonomy: str, term: str) -> int | None:
        return await fetch_wp_term_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/{taxonomy}",
            referer=f"{self.base_url}/",
            term=term,
        )

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        extra: dict[str, str] = {}
        if exclude_ai:
            await self._exclude_ai(extra)
        return await self._wp_or_tag(slug, page, extra=extra)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        slug = resolved.rstrip("/").rsplit("/", 1)[-1]
        posts, _ = await fetch_wp_posts(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/posts",
            page=1,
            referer=f"{self.base_url}/",
            extra={"slug": slug, "per_page": "1", "page": "1"},
        )
        # content.rendered carries the whole gallery in order and without the
        # pagination banners that only appear in the themed HTML pages.
        post = next((entry for entry in posts if entry.get("slug") == slug), None)
        if post is None:
            raise SourceError(f"MissKon post not found: {url}")
        content = wp_content_html(post)
        title = wp_title(post)
        images = list(
            dict.fromkeys(
                image for image in images_from_html(content, self.base_url) if not _is_banner(image)
            )
        )
        if not images:
            raise SourceError(f"MissKon gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=str(post.get("link") or url),
            images=images,
            offset=offset,
            limit=limit,
            tags=wp_terms(post),
            published_at=wp_date(post),
            has_video=looks_like_video(title=title, html=content),
            download_urls=download_urls_from_html(content),
        )

    async def _exclude_ai(self, extra: dict[str, str]) -> None:
        tag_id = await fetch_wp_tag_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/tags",
            referer=f"{self.base_url}/",
            tag="ai-generated",
        )
        if tag_id is not None:
            extra["tags_exclude"] = str(tag_id)

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
        return listing_from_posts(self.id, page, posts, has_next)

    async def _wp_or_tag(
        self, slug: str, page: int, extra: dict[str, str] | None = None
    ) -> ListingPage:
        params = dict(extra or {})
        tag_id = await fetch_wp_tag_id(
            self.http,
            f"{self.base_url}/wp-json/wp/v2/tags",
            referer=f"{self.base_url}/",
            tag=slug,
        )
        if tag_id is None:
            return await self._tag_listing(slug, page)
        params["tags"] = str(tag_id)
        return await self._wp_posts(page, extra=params)

    async def _tag_listing(self, slug: str, page: int) -> ListingPage:
        encoded = quote(slug, safe="-")
        suffix = f"/tag/{encoded}/page/{page}/" if page > 1 else f"/tag/{encoded}/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for node in document.css("article.item-list"):
            link = node.css_first(".post-box-title a")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(link)
            if not href or not title:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(
                        node.css_first(".post-thumbnail img, img"), self.base_url
                    ),
                )
            )
        has_next = (
            document.css_first(".current + a.page, div.pagination > span.current + a") is not None
        )
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def _page_images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for img in document.css("div.entry img"):
            src = img_src(img, self.base_url)
            if src and not _is_banner(src) and src not in seen:
                seen.add(src)
                urls.append(src)
        return urls
