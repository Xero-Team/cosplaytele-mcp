from __future__ import annotations

from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError


class MissKonSource(GallerySource):
    id = "misskon"
    name = "MissKon"
    base_url = "https://misskon.com"
    category_names = ("cosplay",)

    async def popular(self, page: int) -> ListingPage:
        return await self.latest(page)

    async def latest(self, page: int) -> ListingPage:
        suffix = f"/tag/cosplay/page/{page}/" if page > 1 else "/tag/cosplay/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if query.strip():
            url = f"{self.base_url}/page/{page}/?{urlencode({'s': query.strip()})}"
            return await self._listing(url, page)
        return await self.latest(page)

    async def gallery(self, path: str) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(first.css_first(".post-title, h1"))
        if not title:
            raise SourceError(f"MissKon title missing: {url}")
        tags = [text_of(node) for node in first.css(".post-tag > a") if text_of(node)]
        max_page = 1
        numbers = first.css("div.page-link a.post-page-numbers")
        if numbers:
            last = text_of(numbers[-1])
            if last.isdigit():
                max_page = int(last)
        images: list[str] = []
        for page in range(1, max_page + 1):
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
        return Gallery(
            source=self.id,
            title=title,
            path=resolved,
            url=url,
            thumbnail_url=images[0],
            tags=tags,
            image_urls=images,
        )

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
