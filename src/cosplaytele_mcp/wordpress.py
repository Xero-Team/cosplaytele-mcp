from __future__ import annotations

import re
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser

from cosplaytele_mcp.htmlutil import abs_url, img_src, looks_like_video, path_of, slugify
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import ListingItem, ListingPage, SourceId

PAGE_SIZE = 20
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    return unescape(TAG_RE.sub("", value)).strip()


def wp_title(entry: dict[str, Any]) -> str:
    title = entry.get("title")
    if isinstance(title, dict):
        return strip_html(str(title.get("rendered") or ""))
    return strip_html(str(title or ""))


def wp_thumb(entry: dict[str, Any]) -> str | None:
    jetpack = entry.get("jetpack_featured_media_url")
    if isinstance(jetpack, str) and jetpack.strip():
        return jetpack.strip()
    media = (entry.get("_embedded") or {}).get("wp:featuredmedia") or []
    if media and isinstance(media[0], dict):
        url = media[0].get("source_url") or media[0].get("sourceUrl")
        if url:
            return str(url)
    content = entry.get("content")
    if isinstance(content, dict):
        images = images_from_html(str(content.get("rendered") or ""), "https://placeholder.local")
        if images:
            return images[0]
    return None


def wp_terms(entry: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    embedded = entry.get("_embedded") or {}
    for group in embedded.get("wp:term") or embedded.get("terms") or []:
        if not isinstance(group, list):
            continue
        for term in group:
            if not isinstance(term, dict):
                continue
            name = term.get("name") or term.get("slug")
            if name:
                tags.append(str(name))
    return list(dict.fromkeys(tags))


def wp_date(entry: dict[str, Any]) -> str | None:
    raw = entry.get("date") or entry.get("date_gmt")
    if not raw:
        return None
    return str(raw).split("T", 1)[0]


def wp_content_html(entry: dict[str, Any]) -> str:
    content = entry.get("content")
    if not isinstance(content, dict):
        return ""
    return str(content.get("rendered") or "")


def wp_image_count(entry: dict[str, Any], base: str) -> int | None:
    images = images_from_html(wp_content_html(entry), base)
    return len(images) or None


def wp_has_video(entry: dict[str, Any]) -> bool:
    return looks_like_video(title=wp_title(entry), html=wp_content_html(entry))


def images_from_html(html: str, base: str) -> list[str]:
    if not html.strip():
        return []
    document = HTMLParser(html)
    urls: list[str] = []
    seen: set[str] = set()
    for img in document.css("img"):
        src = img_src(img, base)
        if src and not src.startswith("data:") and src not in seen:
            seen.add(src)
            urls.append(src)
    if urls:
        return urls
    for anchor in document.css("a[href]"):
        href = abs_url(base, anchor.attributes.get("href"))
        if (
            href
            and re.search(r"\.(?:jpe?g|png|webp|gif|avif)(?:$|\?)", href, re.I)
            and href not in seen
        ):
            seen.add(href)
            urls.append(href)
    return urls


def listing_from_posts(
    source: SourceId, page: int, posts: list[dict[str, Any]], has_next: bool
) -> ListingPage:
    items: list[ListingItem] = []
    for entry in posts:
        title = wp_title(entry)
        link = str(entry.get("link") or "")
        if not title or not link:
            continue
        items.append(
            ListingItem(
                title=title,
                path=path_of(link),
                url=link,
                thumbnail_url=wp_thumb(entry),
                tags=wp_terms(entry),
                published_at=wp_date(entry),
                image_count=wp_image_count(entry, link),
                has_video=wp_has_video(entry),
            )
        )
    return ListingPage(source=source, page=page, has_next_page=has_next, items=items)


async def fetch_wp_posts(
    http: Http,
    posts_url: str,
    *,
    page: int,
    referer: str,
    search: str | None = None,
    extra: dict[str, str] | None = None,
    timeout: float | None = None,
    embed: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    params: dict[str, Any] = {
        "page": str(page),
        "per_page": str(PAGE_SIZE),
    }
    if embed:
        params["_embed"] = "wp:featuredmedia,wp:term"
    if extra:
        params.update(extra)
    if search and search.strip():
        params["search"] = search.strip()
    response = await http.get(posts_url, referer=referer, params=params, timeout=timeout)
    payload = response.json()
    if isinstance(payload, dict):
        if payload.get("code"):
            return [], False
        payload = [payload]
    if not isinstance(payload, list):
        return [], False
    posts = [entry for entry in payload if isinstance(entry, dict)]
    total_pages = int(response.headers.get("x-wp-totalpages") or 0)
    has_next = page < total_pages if total_pages else len(posts) >= PAGE_SIZE
    return posts, has_next


async def fetch_wp_term_id(
    http: Http,
    terms_url: str,
    *,
    referer: str,
    term: str,
    extra: dict[str, str] | None = None,
) -> int | None:
    slug = slugify(term)
    if not slug:
        return None
    params: dict[str, Any] = {"slug": slug, "per_page": "1"}
    if extra:
        params.update(extra)
    payload = await http.get_json(terms_url, referer=referer, params=params)
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        value = payload[0].get("id")
        return int(value) if value is not None else None
    return None


async def fetch_wp_tag_id(
    http: Http,
    tags_url: str,
    *,
    referer: str,
    tag: str,
    extra: dict[str, str] | None = None,
) -> int | None:
    return await fetch_wp_term_id(http, tags_url, referer=referer, term=tag, extra=extra)
