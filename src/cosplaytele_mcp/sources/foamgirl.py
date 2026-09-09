from __future__ import annotations

from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError


class FoamGirlSource(GallerySource):
    id = "foamgirl"
    name = "FoamGirl"
    base_url = "https://foamgirl.net"
    supports_latest = False
    category_names = ("cosplay",)

    async def popular(self, page: int) -> ListingPage:
        url = f"{self.base_url}/cosplay/page/{page}" if page > 1 else f"{self.base_url}/cosplay"
        return await self._listing(url, page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if not query.strip():
            return await self.popular(page)
        url = f"{self.base_url}/page/{page}/?{urlencode({'post_type': 'post', 's': query.strip()})}"
        return await self._listing(url, page)

    async def gallery(self, path: str) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1, .meta-title, title"))
        if not title:
            raise SourceError(f"FoamGirl title missing: {url}")
        images: list[str] = []
        page_url = url
        for _ in range(40):
            images.extend(self._page_images(document))
            next_link = document.css_first(".page-numbers[title='Next page']")
            href = abs_url(self.base_url, attr(next_link, "href")) if next_link else None
            if not href or href == page_url or "_" not in href.rsplit("/", 1)[-1]:
                break
            page_url = href
            document = await self.http.get_html(page_url, referer=url)
        images = list(dict.fromkeys(images))
        if not images:
            raise SourceError(f"FoamGirl gallery has no images: {url}")
        return Gallery(
            source=self.id,
            title=title,
            path=resolved,
            url=url,
            thumbnail_url=images[0],
            image_urls=images,
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
