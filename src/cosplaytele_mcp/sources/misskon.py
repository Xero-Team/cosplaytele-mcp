from __future__ import annotations

from urllib.parse import quote

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, slugify, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import fetch_wp_posts, fetch_wp_tag_id, listing_from_posts


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
        slug = slugify(category) if category else "cosplay"
        return await self._wp_or_tag(slug, page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if query.strip():
            extra: dict[str, str] = {}
            if exclude_ai:
                await self._exclude_ai(extra)
            return await self._wp_posts(page, search=query.strip(), extra=extra)
        return await self.latest(page, category)

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
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(first.css_first(".post-title, h1"))
        if not title:
            raise SourceError(f"MissKon title missing: {url}")
        tags = [text_of(node) for node in first.css(".post-tag > a") if text_of(node)]
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
        max_page = 1
        numbers = first.css("div.page-link a.post-page-numbers")
        if numbers:
            last = text_of(numbers[-1])
            if last.isdigit():
                max_page = int(last)
        images: list[str] = []
        stopped = False
        for page in range(1, max_page + 1):
            if budget is not None and len(images) >= budget:
                stopped = True
                break
            document = (
                first
                if page == 1
                else await self.http.get_html(
                    f"{url.rstrip('/')}/{page}",
                    referer=url,
                )
            )
            images.extend(self._page_images(document))
        images = list(dict.fromkeys(images))
        if not images:
            raise SourceError(f"MissKon gallery has no images: {url}")
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
        for img in document.css("div.entry img.aligncenter, div.entry img"):
            src = img_src(img, self.base_url)
            if src and "/logo" not in src:
                urls.append(src)
        return urls
