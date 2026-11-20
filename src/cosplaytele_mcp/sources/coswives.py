from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, urlencode

from cosplaytele_mcp.htmlutil import img_src, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError

if TYPE_CHECKING:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

# Next.js App Router embeds its server-component payload in self.__next_f.push
# chunks. Concatenating the chunks recovers one RSC flight document that still
# contains the post arrays as plain JSON.
FLIGHT_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
PHOTOS_RE = re.compile(r"(\d+)\s*photos?", re.IGNORECASE)
VIDEO_RE = re.compile(r"(\d+)\s*videos?", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

CATEGORIES = ("sinful", "lewd", "innocent")


def flight_document(html: str) -> str:
    return "".join(json.loads(chunk) for chunk in FLIGHT_PUSH_RE.findall(html))


def _decode_at(flight: str, start: int) -> Any:
    try:
        value, _ = json.JSONDecoder().raw_decode(flight, start)
    except json.JSONDecodeError:
        return None
    return value


def json_array_after(flight: str, key: str) -> list[Any]:
    """Return the JSON array assigned to `key` in the flight document."""
    match = re.search(rf'"{re.escape(key)}":\[', flight)
    if not match:
        return []
    value = _decode_at(flight, match.end() - 1)
    return value if isinstance(value, list) else []


def first_object_with_key(flight: str, key: str) -> dict[str, Any]:
    match = re.search(rf'\{{[^{{}}]*"{re.escape(key)}":', flight)
    if not match:
        return {}
    value = _decode_at(flight, match.start())
    return value if isinstance(value, dict) else {}


class CosWivesSource(GallerySource):
    id = "coswives"
    name = "CosWives"
    base_url = "https://coswives.com"
    extra_hosts = ("https://coswives-cdn.b-cdn.net",)
    popular_kind = "ranking"
    category_names = CATEGORIES
    image_request_headers: ClassVar[dict[str, str]] = {"Referer": f"{base_url}/"}

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category:
            slug = category.strip().lower()
            if slug not in CATEGORIES:
                raise SourceError(
                    f"Unknown CosWives category {category!r}. Use one of: {', '.join(CATEGORIES)}"
                )
        if period:
            raise SourceError("CosWives popular listings do not support ranking periods")
        if page > 1:
            # /popular is a single fixed top-24 ranking; ?page=N is ignored by the site.
            return ListingPage(source=self.id, page=page, has_next_page=False, items=[])
        url = f"{self.base_url}/popular"
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        posts = json_array_after(flight_document(document.html or ""), "popularPosts")
        items = [self._listing_item(post) for post in posts if isinstance(post, dict)]
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=False,
            items=[item for item in items if item is not None],
        )

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._listing(page, query=None, category=category, tag=None)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        return await self._listing(page, query=query.strip() or None, category=category, tag=None)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        label = tag.strip()
        if not label:
            raise SourceError("tag must not be blank")
        return await self._listing(page, query=None, category=None, tag=label)

    async def _listing(
        self,
        page: int,
        *,
        query: str | None,
        category: str | None,
        tag: str | None,
    ) -> ListingPage:
        params: dict[str, str] = {}
        if query:
            params["q"] = query
        if category:
            slug = category.strip().lower()
            if slug not in CATEGORIES:
                raise SourceError(
                    f"Unknown CosWives category {category!r}. Use one of: {', '.join(CATEGORIES)}"
                )
            params["category"] = category.strip()
        if tag:
            params["tag"] = tag
        if page > 1:
            params["page"] = str(page)
        url = f"{self.base_url}/"
        if params:
            url = f"{url}?{urlencode(params)}"
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        flight = flight_document(document.html or "")
        posts = json_array_after(flight, "posts")
        items = [self._listing_item(post) for post in posts if isinstance(post, dict)]
        total_pages = self._flight_int(flight, "totalPages")
        has_next = page < total_pages if total_pages else len(posts) >= 10
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=has_next,
            items=[item for item in items if item is not None],
        )

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        html = document.html or ""
        flight = flight_document(html)
        gallery_post = first_object_with_key(flight, "imagesArray")
        detail_post = first_object_with_key(flight, "slug")
        title = text_of(document.css_first("h1")) or str(detail_post.get("title") or "").strip()
        if not title:
            raise SourceError(f"CosWives title missing: {url}")
        tags = [text_of(node) for node in document.css('a[href*="/?tag="]') if text_of(node)]
        if not tags:
            tags = [str(tag) for tag in detail_post.get("tags", []) if tag]
        images = [
            str(image)
            for image in json_array_after(flight, "imagesArray")
            if isinstance(image, str) and image.startswith("https://")
        ]
        if not images:
            images = self._dom_images(document)
        if not images:
            raise SourceError(f"CosWives gallery has no images: {url}")
        downloads = [
            str(link["url"])
            for link in json_array_after(flight, "links")
            if isinstance(link, dict) and link.get("url")
        ]
        description = str(gallery_post.get("download_desc") or "")
        has_video = (
            bool(detail_post.get("is_full_video"))
            or bool(detail_post.get("video_preview_url"))
            or bool(VIDEO_RE.search(description))
        )
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            published_at=self._date(str(detail_post.get("created_at") or "")),
            is_ai=False,
            has_video=has_video,
            download_urls=downloads,
        )

    def _listing_item(self, post: dict[str, Any]) -> ListingItem | None:
        slug = str(post.get("slug") or "").strip()
        title = str(post.get("title") or "").strip()
        if not slug or not title:
            return None
        path = f"/post/{quote(slug, safe='-')}"
        description = str(post.get("download_desc") or "")
        photo_match = PHOTOS_RE.search(description)
        thumbnail = str(post.get("thumbnail_url") or "").strip() or None
        return ListingItem(
            title=title,
            path=path,
            url=f"{self.base_url}{path}",
            thumbnail_url=thumbnail,
            published_at=self._date(str(post.get("created_at") or "")),
            image_count=int(photo_match.group(1)) if photo_match else None,
            has_video=bool(post.get("is_full_video"))
            or bool(post.get("video_preview_url"))
            or bool(VIDEO_RE.search(description)),
            tags=[str(tag) for tag in post.get("tags", []) if tag],
        )

    def _dom_images(self, document: HTMLParser) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for node in document.css("article.post-card img, .post-gallery img"):
            src = img_src(node, self.base_url)
            if src and "/webp/" in src and src not in seen:
                seen.add(src)
                images.append(src)
        return images

    @staticmethod
    def _flight_int(flight: str, key: str) -> int:
        match = re.search(rf'"{re.escape(key)}":(\d+)', flight)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _date(value: str) -> str | None:
        match = DATE_RE.search(value)
        return match.group(1) if match else None
