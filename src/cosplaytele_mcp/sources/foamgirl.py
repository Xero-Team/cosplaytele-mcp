from __future__ import annotations

from urllib.parse import quote, urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, slugify, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError


class FoamGirlSource(GallerySource):
    id = "foamgirl"
    name = "FoamGirl"
    base_url = "https://foamgirl.net"
    supports_latest = False
    popular_kind = "archive"
    category_names = ("cosplay",)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        url = f"{self.base_url}/cosplay/page/{page}" if page > 1 else f"{self.base_url}/cosplay"
        return await self._listing(url, page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and category != "cosplay":
            raise SourceError(f"Unknown FoamGirl category {category!r}")
        if not query.strip():
            return await self.popular(page, category)
        prefix = f"/cosplay/page/{page}" if category else f"/page/{page}/"
        url = f"{self.base_url}{prefix}?{urlencode({'post_type': 'post', 's': query.strip()})}"
        return await self._listing(url, page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        encoded = quote(slug, safe="-")
        url = (
            f"{self.base_url}/tag/{encoded}/page/{page}"
            if page > 1
            else f"{self.base_url}/tag/{encoded}"
        )
        return await self._listing(url, page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1, .meta-title, title"))
        if not title:
            raise SourceError(f"FoamGirl title missing: {url}")
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
            )
        images: list[str] = []
        seen_images: set[str] = set()
        page_url = url
        visited_pages: set[str] = set()
        stopped = False
        for _ in range(40):
            visited_pages.add(page_url)
            for image in self._page_images(document):
                if image not in seen_images:
                    seen_images.add(image)
                    images.append(image)
            if budget is not None and len(images) >= budget:
                next_probe = document.css_first(".page-numbers[title='Next page']")
                stopped = attr(next_probe, "href") is not None
                break
            next_link = document.css_first(".page-numbers[title='Next page']")
            href = abs_url(self.base_url, attr(next_link, "href")) if next_link else None
            if not href or href in visited_pages or "_" not in href.rsplit("/", 1)[-1]:
                break
            page_url = href
            document = await self.http.get_html(page_url, referer=url)
        else:
            stopped = (
                attr(document.css_first(".page-numbers[title='Next page']"), "href") is not None
            )
        if not images:
            raise SourceError(f"FoamGirl gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            complete=not stopped,
            has_more=stopped,
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for node in document.css(".update_area .i_list"):
            link = node.css_first("a.meta-title, a.thumb-srcbox, a")
            href = abs_url(self.base_url, attr(link, "href"))
            title = (
                text_of(node.css_first("a.meta-title")) or attr(node.css_first("img"), "alt") or ""
            )
            if not href or not title:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(node.css_first("img"), self.base_url),
                )
            )
        has_next = document.css_first("a.next") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def _page_images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        for node in document.css(".imageclick-imgbox"):
            href = abs_url(self.base_url, attr(node, "href"))
            if href:
                urls.append(href)
        if urls:
            return urls
        for img in document.css(".image-info img, article img, .entry img"):
            src = img_src(img, self.base_url)
            if src:
                urls.append(src)
        return urls
