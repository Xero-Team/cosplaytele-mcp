from __future__ import annotations

import re
from urllib.parse import quote

from selectolax.parser import HTMLParser, Node

from cosplaytele_mcp.htmlutil import (
    abs_url,
    attr,
    img_src,
    looks_like_video,
    path_of,
    slugify,
    text_of,
)
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

HD_PATH = re.compile(r"(/p=\d+x?\d*/)")
RANKINGS = {
    "like": "ranking-like",
    "bookmark": "ranking-bookmark",
    "download": "ranking-download",
    "tag": "ranking-tag",
    "keyword": "ranking-keyword",
    "images": "ranking-images",
}
RANK_PERIODS = ("day", "week", "month", "year")


class HentaiCosplaySource(GallerySource):
    id = "hentaicosplay"
    name = "Hentai Cosplay"
    base_url = "https://hentai-cosplay-xxx.com"
    ranking_kinds = tuple(RANKINGS)

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category not in RANKINGS:
            return await self._category_listing(category, page)
        return await self._parse_listing(self._ranking_url(page, category, period), page)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if category:
            return await self._category_listing(category, page)
        return await self._parse_listing(f"{self.base_url}/search/page/{page}/", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and query.strip():
            raise SourceError(
                "Hentai Cosplay cannot combine keyword search with a category/ranking filter"
            )
        if query:
            keyword = quote(query.strip().replace(" ", "+"), safe="+")
            return await self._parse_listing(
                f"{self.base_url}/search/keyword/{keyword}/page/{page}/", page
            )
        if category:
            return await self._category_listing(category, page)
        return await self.latest(page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        encoded = quote(slug, safe="-")
        return await self._parse_listing(f"{self.base_url}/tag/{encoded}/page/{page}/", page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        details_url = self.absolute(resolved)
        document = await self.http.get_html(details_url, referer=f"{self.base_url}/")
        title = self._title(document)
        if not title:
            raise SourceError(f"Hentai Cosplay title missing: {details_url}")
        tags = [
            text_of(node) for node in document.css("#detail_tag a[href*='/tag/']") if text_of(node)
        ]
        slug = resolved.strip("/").rsplit("/", 1)[-1]
        download = [f"{self.base_url}/download-request/?type=image&url={slug}"]
        has_video = looks_like_video(title=title)
        budget = needed_images(offset, limit)
        if budget == 0:
            return self.make_gallery(
                title=title,
                path=resolved,
                url=details_url,
                images=[],
                offset=offset,
                limit=limit,
                complete=False,
                has_more=True,
                tags=tags,
                has_video=has_video,
                download_urls=download,
            )
        story_path = resolved.replace("/image/", "/story/")
        story_url = self.absolute(story_path)
        story = await self.http.get_html(story_url, referer=details_url)
        images = self._page_images(story)
        if not images:
            raise SourceError(f"Hentai Cosplay gallery has no images: {story_url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=details_url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            has_video=has_video,
            download_urls=download,
        )

    def _ranking_url(self, page: int, category: str | None, period: str | None) -> str:
        kind = RANKINGS.get(category or "", "ranking")
        if period:
            window = period.strip().lower()
            if window not in RANK_PERIODS:
                raise SourceError(
                    f"Unknown Hentai Cosplay period {period!r}. Use one of: {', '.join(RANK_PERIODS)}"
                )
            return f"{self.base_url}/{kind}/type/{window}/page/{page}/"
        return f"{self.base_url}/{kind}/page/{page}/"

    async def _category_listing(self, category: str, page: int) -> ListingPage:
        slug = category.strip().strip("/")
        if not slug.startswith("/"):
            slug = f"/{slug}"
        if not slug.endswith("/"):
            slug = f"{slug}/"
        return await self._parse_listing(f"{self.base_url}{slug}page/{page}/", page)

    async def _parse_listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        desktop = document.css("div.image-list-item")
        if desktop:
            items = [item for node in desktop if (item := self._desktop_item(node))]
            has_next = document.css_first("div.wp-pagenavi > a[rel=next], a[rel=next]") is not None
        else:
            items = [
                item
                for node in document.css("#entry_list a[href*='/image/']")
                if (item := self._mobile_item(node))
            ]
            has_next = document.css_first("a.paginator_page[rel=next], a[rel=next]") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def _title(self, document: HTMLParser) -> str:
        og = attr(document.css_first("meta[property='og:title']"), "content") or ""
        title = og.removesuffix(" - Hentai Cosplay").strip()
        if title:
            return title
        heading = text_of(document.css_first("#display_image_detail_title, h2"))
        if heading and heading.lower() != "hentai cosplay":
            return heading
        page_title = text_of(document.css_first("title"))
        return page_title.removesuffix(" - Hentai Cosplay").strip()

    def _desktop_item(self, node: Node) -> ListingItem | None:
        link = node.css_first("a[href*='/image/']") or node.css_first("a")
        href = abs_url(self.base_url, attr(link, "href"))
        title = text_of(node.css_first(".image-list-item-title")) or text_of(link)
        if not href or "/image/" not in href or not title:
            return None
        thumb = img_src(node.css_first("img"), self.base_url)
        if thumb:
            thumb = thumb.replace("http://", "https://")
        return ListingItem(title=title, path=path_of(href), url=href, thumbnail_url=thumb)

    def _mobile_item(self, node: Node) -> ListingItem | None:
        href = abs_url(self.base_url, attr(node, "href"))
        title = ""
        published = None
        for span in node.css("span"):
            classes = span.attributes.get("class") or ""
            if "posted" in classes.split():
                published = text_of(span).replace("/", "-") or None
                continue
            title = text_of(span)
            if title:
                break
        if not title:
            title = attr(node.css_first("img"), "alt") or ""
        if not href or "/image/" not in href or not title:
            return None
        thumb = img_src(node.css_first("img"), self.base_url)
        if thumb:
            thumb = thumb.replace("http://", "https://")
        return ListingItem(
            title=title,
            path=path_of(href),
            url=href,
            thumbnail_url=thumb,
            published_at=published,
        )

    def _page_images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for node in document.css("amp-img[src*='upload']"):
            classes = node.attributes.get("class") or ""
            if "related-thumbnail" in classes:
                continue
            src = attr(node, "src")
            if not src:
                continue
            cleaned = HD_PATH.sub("/", src.replace("http://", "https://"))
            if cleaned not in seen:
                seen.add(cleaned)
                urls.append(cleaned)
        if urls:
            return urls
        for node in document.css("#display_image_detail img, #detail_list img, img[src*='upload']"):
            src = img_src(node, self.base_url)
            if not src:
                continue
            cleaned = HD_PATH.sub("/", src.replace("http://", "https://"))
            if cleaned not in seen:
                seen.add(cleaned)
                urls.append(cleaned)
        return urls
