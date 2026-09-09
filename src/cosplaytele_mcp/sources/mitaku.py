from __future__ import annotations

from urllib.parse import urlencode

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError

CATEGORIES = {
    "ero-cosplay": "ero-cosplay",
    "nude": "nude",
    "sexy-set": "sexy-set",
    "online-video": "online-video",
}


class MitakuSource(GallerySource):
    id = "mitaku"
    name = "Mitaku"
    base_url = "https://mitaku.net"
    supports_latest = False
    category_names = tuple(CATEGORIES)

    async def popular(self, page: int) -> ListingPage:
        return await self._parse_listing(
            f"{self.base_url}/category/ero-cosplay/page/{page}/",
            page,
        )

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if query:
            url = f"{self.base_url}/page/{page}/?{urlencode({'s': query.strip()})}"
            return await self._parse_listing(url, page)
        if category:
            slug = CATEGORIES.get(category.strip().lower())
            if not slug:
                raise SourceError(
                    f"Unknown Mitaku category {category!r}. Use one of: {', '.join(CATEGORIES)}"
                )
            return await self._parse_listing(f"{self.base_url}/category/{slug}/page/{page}/", page)
        return await self.popular(page)

    async def gallery(self, path: str) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        article = document.css_first("article")
        if article is None:
            raise SourceError(f"Mitaku post details not found: {url}")
        title = text_of(article.css_first("h1"))
        if not title:
            raise SourceError(f"Mitaku title missing: {url}")
        tags = [
            text_of(node)
            for node in article.css("span.cat-links a, span.tag-links a")
            if text_of(node)
        ]
        images = self._images(document)
        if not images:
            raise SourceError(f"Mitaku gallery has no images (video-only posts have none): {url}")
        return Gallery(
            source=self.id,
            title=title,
            path=resolved,
            url=url,
            thumbnail_url=images[0],
            tags=tags,
            image_urls=images,
        )

    async def _parse_listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for node in document.css("div.article-container article"):
            link = node.css_first("a")
            href = abs_url(self.base_url, attr(link, "href"))
            if not href:
                continue
            title = (
                (attr(link, "title") or "").strip()
                or text_of(node.css_first("h1, h2, h3"))
                or text_of(link)
            )
            if not title:
                continue
            items.append(
                ListingItem(
                    title=title,
                    path=path_of(href),
                    url=href,
                    thumbnail_url=img_src(node.css_first("img"), self.base_url),
                )
            )
        has_next = (
            document.css_first("div.wp-pagenavi a.page.larger, a.next.page-numbers") is not None
        )
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def _images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for node in document.css("a.msacwl-img-link"):
            src = abs_url(self.base_url, attr(node, "data-mfp-src")) or abs_url(
                self.base_url, attr(node, "href")
            )
            if src and src not in seen:
                seen.add(src)
                urls.append(src)
        return urls
