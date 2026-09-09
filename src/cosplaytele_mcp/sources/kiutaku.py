from __future__ import annotations

from urllib.parse import urlencode

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError


class KiutakuSource(GallerySource):
    id = "kiutaku"
    name = "Kiutaku"
    base_url = "https://kiutaku.com"

    def _offset(self, page: int) -> int:
        return (page - 1) * 20

    async def popular(self, page: int) -> ListingPage:
        return await self._listing(f"{self.base_url}/hot?start={self._offset(page)}", page)

    async def latest(self, page: int) -> ListingPage:
        return await self._listing(f"{self.base_url}/?start={self._offset(page)}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if not query.strip():
            return await self.latest(page)
        url = f"{self.base_url}/?{urlencode({'search': query.strip(), 'start': str(self._offset(page))})}"
        return await self._listing(url, page)

    async def gallery(self, path: str) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        first = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(first.css_first("div.article-header, h1")) or "Cosplay"
        tags = [
            text_of(node).lstrip("#")
            for node in first.css("div.article-tags a.tag > span")
            if text_of(node)
        ]
        page_urls = [url]
        for node in first.css("nav.pagination a"):
            href = abs_url(self.base_url, attr(node, "href"))
            if href:
                page_urls.append(href)
        images: list[str] = []
        for page_url in list(dict.fromkeys(page_urls))[:40]:
            document = first if page_url == url else await self.http.get_html(page_url, referer=url)
            for img in document.css("div.article-fulltext img[src], div.article-fulltext img"):
                src = img_src(img, self.base_url)
                if src:
                    images.append(src)
        images = list(dict.fromkeys(images))
        if not images:
            raise SourceError(f"Kiutaku gallery has no images: {url}")
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
        for node in document.css("div.blog > div.items-row"):
            link = node.css_first("a.item-link")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(node.css_first("h2")) or "Cosplay"
            if not href:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(node.css_first("img"), self.base_url),
                )
            )
        next_link = document.css_first("a.pagination-next")
        has_next = False
        if next_link is not None:
            classes = (next_link.attributes.get("class") or "").split()
            has_next = "disabled" not in classes and next_link.attributes.get("disabled") is None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)
