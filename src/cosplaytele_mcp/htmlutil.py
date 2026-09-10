from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse, urlsplit

from selectolax.parser import Node

SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
SLUG_DASH_RE = re.compile(r"[-\s]+")


def abs_url(base: str, value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    return urljoin(base if base.endswith("/") else f"{base}/", text)


def img_src(node: Node | None, base: str) -> str | None:
    if node is None:
        return None
    for attr in (
        "data-original",
        "data-original-src",
        "data-lazy-src",
        "data-src",
        "data-mfp-src",
        "file",
    ):
        value = node.attributes.get(attr)
        if not value:
            continue
        text = value.strip()
        if text.lower().startswith("data:") or "blank_" in text or "/images-lazyload" in text:
            continue
        return abs_url(base, text)
    srcset = node.attributes.get("srcset")
    if srcset:
        candidates: list[tuple[int, str]] = []
        for candidate in srcset.split(","):
            parts = candidate.strip().split()
            if not parts:
                continue
            width = int(parts[1][:-1]) if len(parts) > 1 and parts[1].endswith("w") else 0
            candidates.append((width, parts[0]))
        if candidates:
            return abs_url(base, max(candidates)[1])
    src = node.attributes.get("src")
    if src:
        text = src.strip()
        if (
            not text.lower().startswith("data:")
            and "blank_" not in text
            and "/images-lazyload" not in text
        ):
            return abs_url(base, text)
    return None


def text_of(node: Node | None) -> str:
    if node is None:
        return ""
    return unescape(node.text(strip=True))


def attr(node: Node | None, name: str) -> str | None:
    if node is None:
        return None
    value = node.attributes.get(name)
    return value.strip() if value else None


def path_of(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


DOWNLOAD_HOSTS = (
    "gofile.io",
    "sorafolder.com",
    "mega.nz",
    "mega.co.nz",
    "mediafire.com",
    "pixeldrain.com",
    "workupload.com",
    "drive.google.com",
    "dropbox.com",
    "krakenfiles.com",
)

VIDEO_HINT_RE = re.compile(r"\d+\s*videos?", re.IGNORECASE)


def host_key(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def host_belongs(url: str, base_url: str) -> bool:
    host = host_key(url)
    base = host_key(base_url)
    if not host or not base:
        return False
    return host == base or host.endswith(f".{base}")


def looks_like_video(title: str = "", html: str = "") -> bool:
    if title and VIDEO_HINT_RE.search(title):
        return True
    blob = html.lower()
    return "cossora.stream" in blob or "<video" in blob


def download_urls_from_html(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r"""href=["'](https?://[^"']+)""", html, re.I):
        host = host_key(href)
        if (
            any(host == item or host.endswith(f".{item}") for item in DOWNLOAD_HOSTS)
            and href not in seen
        ):
            seen.add(href)
            urls.append(href)
    return urls


def slugify(value: str) -> str:
    text = unescape(value).strip().lower()
    text = SLUG_STRIP_RE.sub("", text)
    return SLUG_DASH_RE.sub("-", text).strip("-")


def normalize_path(path: str, base_url: str) -> str:
    raw = path.strip()
    if not raw:
        raise ValueError("path must not be blank")
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"path is not a valid http(s) URL: {path!r}")
        if not host_belongs(raw, base_url):
            raise ValueError(f"URL host {parsed.hostname} does not belong to {base_url}")
        return path_of(raw)
    # A leading slash normally makes a value relative.  Do not allow it to
    # conceal an absolute URL, which urljoin() would otherwise follow.
    if raw.lstrip("/").lower().startswith(("http:", "https:")):
        raise ValueError("path must not contain an absolute URL")
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw
