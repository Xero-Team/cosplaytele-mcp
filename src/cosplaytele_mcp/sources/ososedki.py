from __future__ import annotations

import re
from urllib.parse import unquote

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, attr, img_src, text_of
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage
from cosplaytele_mcp.sources.base import GallerySource, SourceError

ALBUM_ID = re.compile(r"-?\d+_\d+")
TITLE_SUFFIX = re.compile(
    r"\s*\(\d+\s+leaked\s+photos\)\s+from\s+Onlyfans,\s+Patreon\s+and\s+Fansly\s*$",
    re.IGNORECASE,
)
PHOTO_COUNT = re.compile(r"\((\d+)\s+leaked\s+photos\)", re.IGNORECASE)
DATE_PUBLISHED = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
FILTER_TYPES = {"model", "cosplay", "fandom"}


class OsosedkiSource(GallerySource):
    id = "ososedki"
    name = "OSOSEDKI"
    base_url = "https://ososedki.com"
    category_names = (*sorted(FILTER_TYPES), "cosplays")

    async def popular(
        self, page: int, category: str | None = None, period: str | None = None
    ) -> ListingPage:
        if category:
            return await self._category_albums(category, page, query="")
        return await self._albums(page, type="top", value="1")

    async def latest(self, page: int, category: str | None = None) -> ListingPage:
        if category:
            return await self._category_albums(category, page, query="")
        return await self._albums(page)

    async def search(
        self, query: str, page: int, category: str | None, exclude_ai: bool = True
    ) -> ListingPage:
        if category:
            return await self._category_albums(category, page, query=query)
        if query.strip():
            return await self._albums(page, type="search", value=query.strip())
        return await self.popular(page)

    async def by_tag(self, tag: str, page: int, exclude_ai: bool = True) -> ListingPage:
        label = tag.strip()
        if not label:
            raise SourceError("tag must not be blank")
        kind, _, value = label.partition(":")
        if kind.lower() in FILTER_TYPES and value.strip():
            return await self._albums(page, type=kind.lower(), value=value.strip())
        for filter_type in ("cosplay", "model", "fandom"):
            result = await self._albums(page, type=filter_type, value=label)
            if result.items:
                return result
        return ListingPage(source=self.id, page=page, has_next_page=False, items=[])

    async def gallery(self, path: str, *, offset: int = 0, limit: int | None = None) -> Gallery:
        album_id = self._album_id(path)
        url = f"{self.base_url}/photos/{album_id}"
        document = await self.http.get_html(url, referer=f"{self.base_url}/")
        models = self._link_texts(document, "a[href^='/model/']")
        cosplays = self._link_texts(document, "a[href^='/cosplay/']")
        fandoms = self._link_texts(document, "a[href^='/fandom/']")
        title = " - ".join(part for part in (models[:1] + cosplays[:1] + fandoms[:1]) if part)
        if not title:
            raw = text_of(document.css_first("h1"))
            title = TITLE_SUFFIX.sub("", raw).strip()
        if not title:
            raise SourceError(f"OSOSEDKI title missing for {album_id}")
        published = None
        for script in document.css("script[type='application/ld+json']"):
            match = DATE_PUBLISHED.search(script.text() or "")
            if match:
                published = match.group(1)
                break
        cover = f"{self.base_url}/images/albums/{album_id.replace('_', '/', 1)}.webp"
        images = self._images(document)
        if not images:
            raise SourceError(f"OSOSEDKI gallery has no images: {url}")
        return self.make_gallery(
            title=title,
            path=album_id,
            url=url,
            images=images,
            offset=offset,
            limit=limit,
            thumbnail_url=cover,
            tags=list(dict.fromkeys(models + cosplays + fandoms)),
            published_at=published,
        )

    async def _category_albums(self, category: str, page: int, query: str) -> ListingPage:
        kind, _, value = category.partition(":")
        kind = kind.strip().lower()
        value = value.strip() or query.strip()
        if kind == "cosplays":
            return await self._cosplay_directory(page)
        if kind not in FILTER_TYPES:
            raise SourceError(
                "OSOSEDKI category must be cosplays, or model:/cosplay:/fandom: with a value. "
                f"Got {category!r}"
            )
        if not value:
            raise SourceError("OSOSEDKI category search needs a value, e.g. cosplay:Genshin")
        return await self._albums(page, type=kind, value=value)

    async def _cosplay_directory(self, page: int) -> ListingPage:
        suffix = f"?page={page}" if page > 1 else ""
        document = await self.http.get_html(
            f"{self.base_url}/cosplays{suffix}",
            referer=f"{self.base_url}/",
        )
        items: list[ListingItem] = []
        for node in document.css("a.card[href^='/cosplay/']"):
            href = abs_url(self.base_url, attr(node, "href"))
            if not href:
                continue
            name = unquote(href.rstrip("/").rsplit("/", 1)[-1].replace("+", " "))
            if not name:
                continue
            items.append(
                ListingItem(
                    kind="directory",
                    title=name,
                    path=name,
                    url=href,
                )
            )
        has_next = document.css_first(f"a[href*='cosplays?page={page + 1}']") is not None
        return ListingPage(source=self.id, page=page, has_next_page=has_next, items=items)

    async def _albums(
        self, page: int, type: str | None = None, value: str | None = None
    ) -> ListingPage:
        params: dict[str, str] = {"page": str(page)}
        if type and value:
            params["type"] = type
            params["value"] = value
        payload = await self.http.get_json(
            f"{self.base_url}/api/albums",
            referer=f"{self.base_url}/",
            params=params,
        )
        if not isinstance(payload, dict) or "html" not in payload:
            raise SourceError("OSOSEDKI albums API returned unexpected JSON")
        document = HTMLParser(str(payload["html"]))
        items: list[ListingItem] = []
        for node in document.css("article.gallery-item"):
            link = node.css_first("a.gallery-link")
            href = abs_url(self.base_url, attr(link, "href"))
            if not href:
                continue
            album_id = self._album_id(href)
            title = text_of(node.css_first("h3"))
            if not title:
                img = node.css_first("img.gallery-img")
                alt = (img.attributes.get("alt") if img is not None else "") or ""
                title = alt.split(" nude.")[0].strip()
            if not title:
                continue
            count_match = PHOTO_COUNT.search(title)
            image_count = int(count_match.group(1)) if count_match else None
            items.append(
                ListingItem(
                    title=title,
                    path=album_id,
                    url=f"{self.base_url}/photos/{album_id}",
                    thumbnail_url=img_src(node.css_first("img.gallery-img"), self.base_url),
                    image_count=image_count,
                )
            )
        return ListingPage(
            source=self.id,
            page=page,
            has_next_page=bool(payload.get("hasMore")),
            items=items,
        )

    def _album_id(self, path: str) -> str:
        resolved = path.strip()
        if resolved.startswith("http"):
            resolved = self.resolve_path(resolved)
        parts = [part for part in resolved.split("/") if part]
        album_id = parts[parts.index("photos") + 1] if "photos" in parts else parts[-1]
        if not ALBUM_ID.fullmatch(album_id):
            raise SourceError(f"Invalid OSOSEDKI album id: {path}")
        return album_id

    def _images(self, document: HTMLParser) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for node in document.css("#photos a"):
            href = abs_url(self.base_url, attr(node, "href"))
            if not href or "/images/" not in href:
                continue
            if href not in seen:
                seen.add(href)
                urls.append(href)

        def sort_key(url: str) -> int:
            name = url.rsplit("/", 1)[-1].split(".", 1)[0]
            try:
                return int(name)
            except ValueError:
                return 10**9

        return sorted(urls, key=sort_key)

    def _link_texts(self, document: HTMLParser, selector: str) -> list[str]:
        values: list[str] = []
        for node in document.css(selector):
            label = text_of(node)
            if label and label not in values:
                values.append(label)
        return values
