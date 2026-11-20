from __future__ import annotations

import json
import re
from html import unescape
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote, urlencode, urlsplit

from cosplaytele_mcp.ai import looks_like_ai
from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, path_of, slugify, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError

if TYPE_CHECKING:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

SET_PREFIX = "/sets/"
COUNT_RE = re.compile(r"\d+")
WHITESPACE_RE = re.compile(r"\s+")


class SimplyCosplaySource(GallerySource):
    id = "simplycosplay"
    name = "Simply Cosplay"
    base_url = "https://www.simply-cosplay.com"
    extra_hosts = ("https://images.sh-cdn.com",)
    image_request_headers: ClassVar[dict[str, str]] = {"Referer": f"{base_url}/"}

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        self._reject_category(category)
        return await self._listing(self._sets_url(page, sort="hot"), page)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        self._reject_category(category)
        return await self._listing(self._sets_url(page), page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        self._reject_category(category)
        text = query.strip()
        if not text:
            return await self.latest(page)
        params = {"q": text, "type": "set"}
        if page > 1:
            params["page"] = str(page)
        return await self._listing(f"{self.base_url}/search?{urlencode(params)}", page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        slug = slugify(tag)
        if not slug:
            raise SourceError("tag must not be blank")
        if page > 1:
            # Tag pages list every set at once and answer ?page=2 with a 404.
            return ListingPage(source=self.id, page=page, has_next_page=False, items=[])
        url = f"{self.base_url}/tags/{quote(slug, safe='-')}"
        return await self._listing(url, page)

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self._set_path(self.resolve_path(path))
        url = self.absolute(resolved)
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        jsonld = _jsonld_gallery(document)
        title = _squash_node(document.css_first("h1")) or str(jsonld.get("name") or "").strip()
        if not title:
            raise SourceError(f"Simply Cosplay set title missing: {url}")
        tags = self._tags(document)
        images = self._gallery_images(document, jsonld)
        if not images:
            raise SourceError(f"Simply Cosplay set has no images: {url}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            tags=tags,
            is_ai=looks_like_ai(title=title, path=resolved, tags=tags),
        )

    def _sets_url(self, page: int, sort: str | None = None) -> str:
        params: dict[str, str] = {}
        if sort:
            params["sort"] = sort
        if page > 1:
            params["page"] = str(page)
        query = f"?{urlencode(params)}" if params else ""
        return f"{self.base_url}/sets{query}"

    def _set_path(self, resolved: str) -> str:
        path = urlsplit(resolved).path.rstrip("/")
        slug = path.removeprefix(SET_PREFIX)
        if slug == path or not slug or "/" in slug:
            raise SourceError(f"Simply Cosplay path is not a set: {resolved}")
        return resolved

    def _reject_category(self, category: str | None) -> None:
        if category and category.strip():
            raise SourceError(
                "Simply Cosplay has no category listings; browse tags with browse_tag instead"
            )

    async def _listing(self, url: str, page: int) -> ListingPage:
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=document.css_first('link[rel="next"]') is not None,
            items=self._items(document),
        )

    def _items(self, document: HTMLParser) -> list[ListingItem]:
        items: list[ListingItem] = []
        seen: set[str] = set()
        for card in document.css(f'a.th[href^="{SET_PREFIX}"]'):
            href = abs_url(self.base_url, attr(card, "href"))
            if not href:
                continue
            path = path_of(href)
            if path in seen:
                continue
            seen.add(path)
            title = _card_title(card) or path
            items.append(
                ListingItem(
                    title=title,
                    path=path,
                    url=href,
                    thumbnail_url=img_src(card.css_first(".im img, img"), self.base_url),
                    image_count=_card_count(card),
                    is_ai=looks_like_ai(title=title, path=path),
                )
            )
        return items

    def _tags(self, document: HTMLParser) -> list[str]:
        labels = (text_of(node) for node in document.css('[data-testid="set-tags"] a'))
        return [label for label in dict.fromkeys(labels) if label]

    def _gallery_images(self, document: HTMLParser, jsonld: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for img in document.css(".film a img"):
            src = img_src(img, self.base_url)
            if src and src not in seen:
                seen.add(src)
                urls.append(src)
        if urls:
            return urls
        for raw in jsonld.get("image") or []:
            if isinstance(raw, str) and raw.strip() and raw.strip() not in seen:
                seen.add(raw.strip())
                urls.append(raw.strip())
        return urls


def _squash(value: str) -> str:
    """Collapse the whitespace JSX leaves around inline elements."""
    return WHITESPACE_RE.sub(" ", unescape(value)).strip()


def _squash_node(node: Node | None) -> str:
    # strip=True drops the space JSX puts between an inline element and the
    # text that follows it, so normalize the raw text instead.
    return _squash(node.text()) if node is not None else ""


def _card_title(card: Node) -> str:
    return _squash_node(card.css_first("h3"))


def _card_count(card: Node) -> int | None:
    node = card.css_first(".n")
    if node is None:
        return None
    match = COUNT_RE.search(node.text())
    return int(match.group()) if match else None


def _jsonld_gallery(document: HTMLParser) -> dict[str, Any]:
    for node in document.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "ImageGallery":
            return data
    return {}
