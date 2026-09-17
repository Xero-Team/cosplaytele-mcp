from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

import httpx
from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage, needed_images
from cosplaytele_mcp.sources.base import GallerySource, SourceError

API_BASE = "https://api.simply-porn.com/v2"
PAGE_SIZE = 20
FALLBACK_TOKEN = "01730876"
GALLERY_KINDS = frozenset({"gallery", "image"})
TOKEN_RE = re.compile(r'token\s*:\s*"([^"]+)"')
MAIN_SCRIPT_RE = re.compile(r"main[^\"']*\.js", re.I)


class SimplyCosplaySource(GallerySource):
    id = "simplycosplay"
    name = "Simply Cosplay"
    base_url = "https://www.simply-cosplay.com"
    extra_hosts = ("https://api.simply-porn.com",)
    category_names = ("gallery", "image")
    image_request_headers: ClassVar[dict[str, str]] = {"Referer": f"{base_url}/"}

    def __init__(self, http: Http) -> None:
        super().__init__(http)
        self._token = FALLBACK_TOKEN
        self._token_lock = asyncio.Lock()

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        return await self._browse(page, sort="hot", category=category)

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        return await self._browse(page, sort="new", category=category)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if not query.strip():
            return await self.latest(page, category)
        params: dict[str, Any] = {
            "sort": "new",
            "limit": str(PAGE_SIZE),
            "page": str(page),
            "query": query.strip(),
        }
        kind = self._category_kind(category)
        if kind:
            params["filter[type][0]"] = kind
        payload = await self._api_get("search", params)
        items = self._listing_items(payload)
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=len(items) >= PAGE_SIZE,
            items=items,
        )

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        resolved = self.resolve_path(path)
        kind, slug = self._kind_and_slug(resolved)
        payload = await self._api_get(f"{kind}/{slug}", {})
        data = _unwrap(payload)
        if not isinstance(data, dict):
            raise SourceError(f"Simply Cosplay gallery payload is not an object: {resolved}")
        title = str(data.get("title") or slug).strip() or slug
        tags = [
            str(tag.get("name") or "").strip()
            for tag in data.get("tags") or []
            if isinstance(tag, dict) and str(tag.get("name") or "").strip()
        ]
        published = _preview_date(data.get("preview"))
        total = data.get("image_count")
        total_int = int(total) if isinstance(total, int) else None
        budget = needed_images(offset, limit)
        if budget == 0:
            return self.make_gallery(
                title=title,
                path=resolved,
                url=self.absolute(resolved),
                images=[],
                offset=offset,
                limit=limit,
                complete=False,
                total=total_int,
                has_more=True,
                thumbnail_url=_thumb(data.get("preview")),
                tags=tags,
                published_at=published,
            )
        images = self._gallery_images(data)
        if not images:
            raise SourceError(f"Simply Cosplay gallery has no images: {resolved}")
        return self.make_gallery(
            title=title,
            path=resolved,
            url=self.absolute(resolved),
            images=images,
            offset=offset,
            limit=limit,
            total=total_int,
            thumbnail_url=_thumb(data.get("preview")) or images[0],
            tags=tags,
            published_at=published,
        )

    async def _browse(self, page: int, *, sort: str, category: str | None) -> ListingPage:
        kind = self._category_kind(category) or "gallery"
        payload = await self._api_get(
            kind,
            {"sort": sort, "limit": str(PAGE_SIZE), "page": str(page)},
        )
        items = self._listing_items(payload)
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=len(items) >= PAGE_SIZE,
            items=items,
        )

    async def _api_get(self, path: str, params: dict[str, Any]) -> Any:
        query = dict(params)
        query["token"] = self._token
        url = f"{API_BASE}/{path.strip('/')}"
        try:
            return await self.http.get_json(url, referer=f"{self.base_url}/", params=query)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            async with self._token_lock:
                if query["token"] != self._token:
                    query["token"] = self._token
                    return await self.http.get_json(url, referer=f"{self.base_url}/", params=query)
                self._token = await self._fetch_token()
                query["token"] = self._token
                return await self.http.get_json(url, referer=f"{self.base_url}/", params=query)

    async def _fetch_token(self) -> str:
        document = await self.http.get_html(self.base_url, referer=f"{self.base_url}/", cache=False)
        script = _main_script_src(document, self.base_url)
        if not script:
            raise SourceError("Simply Cosplay token script was not found")
        response = await self.http.get(script, referer=f"{self.base_url}/", cache=False)
        token = token_from_script(response.text)
        if not token:
            raise SourceError("Simply Cosplay token was not found in the main script")
        return token

    def _listing_items(self, payload: Any) -> list[ListingItem]:
        data = _unwrap(payload)
        if not isinstance(data, list):
            return []
        items: list[ListingItem] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            slug = str(raw.get("slug") or "").strip()
            kind = str(raw.get("type") or "gallery").strip().lower() or "gallery"
            title = str(raw.get("title") or slug).strip()
            if kind not in GALLERY_KINDS or not slug or not title:
                continue
            path = f"/{kind}/new/{slug}"
            items.append(
                ListingItem(
                    title=title,
                    path=path,
                    url=self.absolute(path),
                    thumbnail_url=_thumb(raw.get("preview")),
                    published_at=_preview_date(raw.get("preview")),
                )
            )
        return items

    def _gallery_images(self, data: dict[str, Any]) -> list[str]:
        images: list[str] = []
        seen: set[str] = set()
        for raw in data.get("images") or []:
            if not isinstance(raw, dict):
                continue
            url = _full(raw)
            if url and url not in seen:
                seen.add(url)
                images.append(url)
        if images:
            return images
        preview = _full(data.get("preview"))
        return [preview] if preview else []

    def _kind_and_slug(self, path: str) -> tuple[str, str]:
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) >= 3 and parts[1] == "new":
            kind, slug = parts[0].lower(), parts[2]
        elif len(parts) >= 2:
            kind, slug = parts[0].lower(), parts[1]
        else:
            raise SourceError(f"Simply Cosplay path is not a gallery: {path}")
        if kind not in GALLERY_KINDS or not slug:
            raise SourceError(f"Simply Cosplay path is not a gallery: {path}")
        return kind, slug

    def _category_kind(self, category: str | None) -> str | None:
        if not category:
            return None
        kind = category.strip().lower()
        if kind not in GALLERY_KINDS:
            raise SourceError(f"Unknown Simply Cosplay category {category!r}")
        return kind


def token_from_script(script: str) -> str | None:
    match = TOKEN_RE.search(script.replace("'", '"'))
    return match.group(1) if match else None


def _main_script_src(document: HTMLParser, base: str) -> str | None:
    for node in document.css("script[src]"):
        src = attr(node, "src") or ""
        if MAIN_SCRIPT_RE.search(src):
            return abs_url(base, src)
    return None


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _preview_urls(preview: Any) -> dict[str, Any]:
    if not isinstance(preview, dict):
        return {}
    urls = preview.get("urls")
    return urls if isinstance(urls, dict) else {}


def _thumb(preview: Any) -> str | None:
    urls = _preview_urls(preview)
    thumb = urls.get("thumb")
    if isinstance(thumb, dict):
        url = thumb.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    full = urls.get("url")
    return full.strip() if isinstance(full, str) and full.strip() else None


def _full(preview: Any) -> str | None:
    urls = _preview_urls(preview)
    full = urls.get("url")
    if isinstance(full, str) and full.strip():
        return full.strip()
    return _thumb(preview)


def _preview_date(preview: Any) -> str | None:
    if not isinstance(preview, dict):
        return None
    value = preview.get("publish_date")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:10]
