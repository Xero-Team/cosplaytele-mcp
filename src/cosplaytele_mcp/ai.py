from __future__ import annotations

import re

from cosplaytele_mcp.models import ListingPage

AI_TITLE_RE = re.compile(
    r"(?:^|[\s(\[{「『])(?:ai[\s_-]?art|ai[\s_-]?generated|ai[\s_-]?enhanced|ai生成|ai繪圖|ai绘图)(?:$|[\s)\]}」』:,.!?–-])",
    re.IGNORECASE,
)
AI_SLUG_RE = re.compile(
    r"(?:^|[-/_])ai[-_]?(?:art|generated|enhanced)(?:$|[-/_])",
    re.IGNORECASE,
)
AI_TAG_SLUGS = {
    "ai-art",
    "ai-generated",
    "ai-enhanced",
    "ai生成",
    "ai绘图",
    "ai繪圖",
}


def looks_like_ai(
    *, title: str = "", path: str = "", tags: list[str] | tuple[str, ...] = ()
) -> bool:
    if title and AI_TITLE_RE.search(title):
        return True
    if path and AI_SLUG_RE.search(path):
        return True
    for tag in tags:
        slug = tag.strip().lower().replace(" ", "-")
        if slug in AI_TAG_SLUGS:
            return True
        if tag and AI_TITLE_RE.search(tag):
            return True
    return False


def apply_ai_filter(page: ListingPage, exclude_ai: bool) -> ListingPage:
    items = []
    for item in page.items:
        flagged = item.model_copy(
            update={
                "is_ai": item.is_ai
                or looks_like_ai(title=item.title, path=item.path, tags=item.tags),
            },
        )
        if exclude_ai and flagged.is_ai:
            continue
        items.append(flagged)
    return page.model_copy(update={"items": items})
