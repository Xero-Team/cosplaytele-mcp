import pytest
from selectolax.parser import HTMLParser

from cosplaytele_mcp.ai import apply_ai_filter, looks_like_ai
from cosplaytele_mcp.htmlutil import normalize_path
from cosplaytele_mcp.models import ListingItem, ListingPage
from cosplaytele_mcp.sources.cosplaytele import CosplayTeleSource
from cosplaytele_mcp.sources.hentaicosplay import HentaiCosplaySource
from cosplaytele_mcp.sources.ososedki import OsosedkiSource

GALLERY_HTML = """
<html><body>
<h1 class="entry-title">Kisaki Gallery</h1>
<time class="updated" datetime="2026-09-09T18:22:18+08:00"></time>
<div id="main">
  <a href="https://cosplaytele.com/category/cosplay/">Cosplay</a>
  <a href="https://cosplaytele.com/tag/blue-archive/">Blue Archive</a>
</div>
<div class="gallery">
  <figure class="gallery-item"><img src="https://cosplaytele.com/wp-content/uploads/a.webp"></figure>
  <figure class="gallery-item"><img src="https://cosplaytele.com/wp-content/uploads/b.webp"></figure>
</div>
</body></html>
"""

HC_LISTING_HTML = """
<html><body>
<div class="image-list-item">
  <a href="/image/aqua-birthday-bunny/">
    <img src="https://static.example/upload/p=160x200/22.webp">
  </a>
  <div class="image-list-item-title">Aqua</div>
</div>
<div class="wp-pagenavi"><a rel="next" href="/ranking/page/2/">2</a></div>
</body></html>
"""


def test_looks_like_ai() -> None:
    assert looks_like_ai(title="AI Art – Anime Girl 81 – Luo Li")
    assert looks_like_ai(title="Aqua Birthday Bunny (AI Generated)")
    assert looks_like_ai(title="Taylor Swift ai-generated Kansas City Chiefs")
    assert looks_like_ai(path="/image/aqua-birthday-bunny-ai-generated/")
    assert looks_like_ai(tags=["ai-art"])
    assert looks_like_ai(tags=["AI Enhanced"])
    assert not looks_like_ai(title="Ai Yamada 山田あい, 週プレ Photo Book")
    assert not looks_like_ai(title="Ai Hoshino")
    assert not looks_like_ai(title="咬一口兔娘ovo cosplay Ryuuge Kisaki")


def test_normalize_path_accepts_url_and_relative() -> None:
    base = "https://cosplaytele.com"
    assert normalize_path("https://cosplaytele.com/ryuuge-kisaki-4/", base) == "/ryuuge-kisaki-4/"
    assert normalize_path("ryuuge-kisaki-4/", base) == "/ryuuge-kisaki-4/"


def test_normalize_path_rejects_other_host() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        normalize_path("https://example.com/x", "https://cosplaytele.com")


def test_apply_ai_filter_drops_marked_items() -> None:
    page = ListingPage(
        source="cosplaytele",
        page=1,
        has_next_page=False,
        items=[
            ListingItem(title="Kisaki", path="/kisaki/", url="https://cosplaytele.com/kisaki/"),
            ListingItem(
                title="AI Art – Girl",
                path="/ai-art-girl/",
                url="https://cosplaytele.com/ai-art-girl/",
            ),
        ],
    )
    filtered = apply_ai_filter(page, exclude_ai=True)
    assert [item.title for item in filtered.items] == ["Kisaki"]
    kept = apply_ai_filter(page, exclude_ai=False)
    assert [item.is_ai for item in kept.items] == [False, True]


def test_cosplaytele_gallery_images() -> None:
    source = CosplayTeleSource(http=None)  # type: ignore[arg-type]
    gallery = HTMLParser(GALLERY_HTML)
    images = source._gallery_images(gallery)
    assert images == [
        "https://cosplaytele.com/wp-content/uploads/a.webp",
        "https://cosplaytele.com/wp-content/uploads/b.webp",
    ]
    assert source._tags(gallery) == ["Cosplay", "Blue Archive"]


def test_hentaicosplay_title_from_og() -> None:
    source = HentaiCosplaySource(http=None)  # type: ignore[arg-type]
    document = HTMLParser(
        """
        <html><head>
          <title>Aqua Birthday Bunny (AI Generated) - Hentai Cosplay</title>
          <meta property="og:title" content="Aqua Birthday Bunny (AI Generated) - Hentai Cosplay">
        </head>
        <body><h1 id="site_title">Hentai Cosplay</h1><h2>Aqua Birthday Bunny (AI Generated)</h2></body></html>
        """
    )
    assert source._title(document) == "Aqua Birthday Bunny (AI Generated)"


def test_cosplaytele_wp_search_item() -> None:
    source = CosplayTeleSource(http=None)  # type: ignore[arg-type]
    item = source._from_wp_post(
        {
            "title": {"rendered": "Yaokoututu cosplay Ryuuge Kisaki &#8211; Blue Archive"},
            "link": "https://cosplaytele.com/ryuuge-kisaki-4/",
            "_embedded": {
                "wp:featuredmedia": [{"source_url": "https://cosplaytele.com/cover.webp"}]
            },
        }
    )
    assert item.path == "/ryuuge-kisaki-4/"
    assert "Kisaki" in item.title
    assert "–" in item.title or "-" in item.title
    assert item.thumbnail_url.endswith("cover.webp")


def test_hentaicosplay_mobile_item() -> None:
    source = HentaiCosplaySource(http=None)  # type: ignore[arg-type]
    node = HTMLParser(
        """
        <ul id="entry_list">
          <li>
            <a href="/image/hanasaru-aqua/">
              <img src="https://static.example/8.webp" alt="Hanasaru - Aqua">
              <span class="posted">2026/09/08</span>
              <span>Hanasaru - Aqua</span>
            </a>
          </li>
        </ul>
        """
    ).css_first("#entry_list a")
    item = source._mobile_item(node)
    assert item is not None
    assert item.path == "/image/hanasaru-aqua/"
    assert item.title == "Hanasaru - Aqua"


def test_hentaicosplay_desktop_item() -> None:
    source = HentaiCosplaySource(http=None)  # type: ignore[arg-type]
    node = HTMLParser(HC_LISTING_HTML).css_first("div.image-list-item")
    item = source._desktop_item(node)
    assert item is not None
    assert item.path == "/image/aqua-birthday-bunny/"
    assert item.title == "Aqua"


def test_ososedki_album_id() -> None:
    source = OsosedkiSource(http=None)  # type: ignore[arg-type]
    assert source._album_id("/photos/-10000001_10016121") == "-10000001_10016121"
    assert source._album_id("-10000001_10016121") == "-10000001_10016121"
