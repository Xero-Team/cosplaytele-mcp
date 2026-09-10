from __future__ import annotations

import re

from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage

ADULT_LABEL = "(18+)"

# Theme labels can look age-coded when shown without context. These are presentation
# annotations only: source paths, search queries, and source-provided metadata stay intact.
ADULT_THEME_TERMS = (
    "school\\s+swimsuit",
    "school\\s+uniform",
    "sailor\\s+uniform",
    "high\\s+school\\s+girl",
    "school\\s+girl",
    "after\\s+school",
    "student\\s+(?:girl|uniform)",
    "petite\\s+girl",
    "barely\\s+legal",
    "schoolgirl",
    "high\\s+school",
    "uniform",
    "prefect",
    r"(?<![A-Za-z])lolita(?![A-Za-z])",
    r"(?<![A-Za-z])loli(?![A-Za-z])",
    "JK",
    "女高中生",
    "女子高生",
    "女子校生",
    "学生服",
    "スクール水着",
    "セーラー服",
    "風紀委員",
    "放課後",
    "ロリータ",
    "ロリ",
    "学校泳衣",
    "学生装",
    "学生妹",
    "女学生",
    "风纪委员",
    "風紀委員",
    "死库水",
    "死庫水",
    "水手服",
    "放学后",
    "放學後",
    "幼态",
    "幼態",
    "萝莉",
    "蘿莉",
    "校服",
    "制服",
    "学妹",
    "學妹",
    "美少女",
    "少女",
)
ADULT_THEME_RE = re.compile(rf"(?:{'|'.join(ADULT_THEME_TERMS)})", re.IGNORECASE)


def label_adult_theme_text(text: str) -> str:
    """Append an adult-context label to each age-coded theme term in display text."""

    def replace(match: re.Match[str]) -> str:
        if re.match(r"\s*\(18\+\)", text[match.end() :]):
            return match.group(0)
        return f"{match.group(0)} {ADULT_LABEL} "

    labeled = re.sub(r"\(18\+\)[ \t]+", f"{ADULT_LABEL} ", ADULT_THEME_RE.sub(replace, text))
    return labeled.rstrip() if labeled != text else text


def label_listing_item(item: ListingItem) -> ListingItem:
    title = label_adult_theme_text(item.title)
    tags = [label_adult_theme_text(tag) for tag in item.tags]
    if title == item.title and tags == item.tags:
        return item
    return item.model_copy(update={"title": title, "tags": tags})


def apply_adult_labels(page: ListingPage) -> ListingPage:
    return page.model_copy(update={"items": [label_listing_item(item) for item in page.items]})


def label_gallery(gallery: Gallery) -> Gallery:
    title = label_adult_theme_text(gallery.title)
    tags = [label_adult_theme_text(tag) for tag in gallery.tags]
    if title == gallery.title and tags == gallery.tags:
        return gallery
    return gallery.model_copy(update={"title": title, "tags": tags})
