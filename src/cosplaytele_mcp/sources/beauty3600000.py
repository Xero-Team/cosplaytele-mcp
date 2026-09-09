from __future__ import annotations

from urllib.parse import quote, urlencode

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, slugify, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.wordpress import images_from_html


class Beauty3600000Source(GallerySource):
    id = "beauty3600000"
    name = "3600000 Beauty"
    base_url = "https://3600000.xyz"
    supports_latest = False
    category_names = ("cosplay",)

    async def popular(self, page: int, category: str | None = None) -> ListingPage:
        suffix = f"/category/cosplay/page/{page}/" if page > 1 else "/category/cosplay/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if not query.strip():
            return await self.popular(page)
        path = f"/page/{page}/" if page > 1 else "/"
        url = f"{self.base_url}{path}?{urlencode({'s': query.strip()})}"
        return await self._listing(url, page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        encoded = quote(slug, safe="-")
        suffix = f"/tag/{encoded}/page/{page}/" if page > 1 else f"/tag/{encoded}/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1.entry-title, .entry-title, h1"))
        if not title:
            raise SourceError(f"3600000 title missing: {url}")
        tags = [text_of(node) for node in document.css("a[rel=tag]") if text_of(node)]
        content = document.css_first(".entry-content, article")
        html = content.html if content is not None else document.html or ""
        images = images_from_html(html, self.base_url)
        images = [src for src in images if "gravatar" not in src]
        if not images:
            raise SourceError(f"3600000 gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items: list[ListingItem] = []
        for node in document.css("article"):
            link = node.css_first(".entry-title a, h2 a, a[rel=bookmark]")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(link)
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
        has_next = document.css_first("a.next, a.next.page-numbers") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)
