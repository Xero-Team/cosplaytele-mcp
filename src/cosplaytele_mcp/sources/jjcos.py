from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote, unquote

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, text_of
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

COSPLAY_TAG = "HSQ2151O0wZ"
PAGE_SIZE = 20
INDEX_TTL = 3600.0
INDEX_MAX_POSTS = 5000
TITLE_SUFFIX = " - JJCOS"


class JJCOSSource(GallerySource):
    id = "jjcos"
    name = "JJCOS"
    base_url = "https://jjcos.com"
    supports_latest = False
    popular_kind = "archive"
    category_names = ("cosplay",)

    def __init__(self, http: Http) -> None:
        super().__init__(http)
        self._index_posts: list[dict[str, Any]] | None = None
        self._index_at = 0.0

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("JJCOS only supports the cosplay category")
        suffix = f"/tag/{COSPLAY_TAG}/page/{page}" if page > 1 else f"/tag/{COSPLAY_TAG}/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category and category.strip().lower() != "cosplay":
            raise SourceError("JJCOS only supports the cosplay category")
        if not query.strip():
            return await self.popular(page, category)
        posts = await self._index()
        needle = query.strip().lower()
        matched = [post for post in posts if needle in post["haystack"]]
        start = (page - 1) * PAGE_SIZE
        chunk = matched[start : start + PAGE_SIZE]
        items = [self._item_from_post(post) for post in chunk]
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=start + PAGE_SIZE < len(matched),
            items=items,
        )

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = tag.strip()
        if not slug:
            raise SourceError("tag must not be blank")
        if slug.lower() == "cosplay":
            slug = COSPLAY_TAG
        encoded = quote(slug, safe="-")
        suffix = f"/tag/{encoded}/page/{page}" if page > 1 else f"/tag/{encoded}/"
        return await self._listing(f"{self.base_url}{suffix}", page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        title = text_of(document.css_first("h1.fh5co-article-title, h1, title"))
        title = title.removesuffix(" | JJCOS").removesuffix(TITLE_SUFFIX).strip()
        if not title:
            raise SourceError(f"JJCOS title missing: {url}")
        tags = []
        seen_tags: set[str] = set()
        for node in document.css("a.tag"):
            label = text_of(node).lstrip("#").strip()
            if label and label.lower() not in seen_tags:
                seen_tags.add(label.lower())
                tags.append(label)
        published = attr(document.css_first('meta[property="article:published_time"]'), "content")
        if published:
            published = published.strip()[:10]
        else:
            published = text_of(document.css_first(".date-overlay")).rstrip("|").strip() or None
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
                published_at=published,
            )
        images = self._page_images(document)
        if not images:
            raise SourceError(f"JJCOS gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            published_at=published,
        )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        items = self.listing_items(document)
        has_next = document.css_first(f'a[href*="/page/{page + 1}"]') is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    def listing_items(self, document: HTMLParser) -> list[ListingItem]:
        items: list[ListingItem] = []
        for node in document.css("article.custom-article"):
            link = node.css_first(".fh5co-article-title a")
            href = abs_url(self.base_url, attr(link, "href"))
            title = text_of(link)
            if not href or not title:
                continue
            tags = [
                text_of(tag).lstrip("#").strip()
                for tag in node.css("a.tag")
                if text_of(tag).lstrip("#").strip()
            ]
            published = text_of(node.css_first(".date-overlay")).rstrip("|").strip() or None
            items.append(
                ListingItem(
                    title=title,
                    path=encoded_path(href),
                    url=href,
                    thumbnail_url=img_src(node.css_first("figure img"), self.base_url),
                    tags=tags,
                    published_at=published,
                )
            )
        return items

    def _page_images(self, document: HTMLParser) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for img in document.css("#post-content img"):
            src = img_src(img, self.base_url)
            if src and src not in seen:
                seen.add(src)
                images.append(src)
        return images

    async def _index(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._index_posts is not None and now - self._index_at < INDEX_TTL:
            return self._index_posts
        payload = await self.http.get_json(
            f"{self.base_url}/api/index.html",
            referer=f"{self.base_url}/",
        )
        posts = posts_from_index(payload)
        self._index_posts = posts
        self._index_at = now
        return posts

    def _item_from_post(self, post: dict[str, Any]) -> ListingItem:
        href = abs_url(self.base_url, str(post["link"])) or post["link"]
        published = str(post.get("dateFormat") or "").strip() or None
        thumb = post.get("feature")
        return ListingItem(
            title=post["title"],
            path=encoded_path(href),
            url=href,
            thumbnail_url=str(thumb).strip() if isinstance(thumb, str) and thumb.strip() else None,
            published_at=published[:10] if published else None,
        )


def encoded_path(url: str) -> str:
    return quote(unquote(path_of(url)), safe="/")


def _tag_blob(raw: dict[str, Any]) -> str:
    tags = raw.get("tags") or raw.get("tag") or raw.get("labels")
    if isinstance(tags, list):
        return " ".join(str(tag) for tag in tags if tag)
    if isinstance(tags, str):
        return tags
    return ""


def posts_from_index(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or "posts" not in payload:
        raise SourceError("JJCOS index payload is missing posts")
    raw_posts = payload["posts"]
    if not isinstance(raw_posts, list):
        raise SourceError("JJCOS index posts is not a list")
    posts: list[dict[str, Any]] = []
    for raw in raw_posts[:INDEX_MAX_POSTS]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        link = str(raw.get("link") or "").strip()
        if not title or not link:
            continue
        haystack = f"{title} {link} {_tag_blob(raw)}".lower()
        if "cosplay" not in haystack:
            continue
        posts.append(
            {
                "title": title,
                "link": link,
                "feature": raw.get("feature"),
                "dateFormat": raw.get("dateFormat"),
                "haystack": haystack,
            }
        )
    return posts
