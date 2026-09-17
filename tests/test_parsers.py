import pytest
from selectolax.parser import HTMLParser

from cosplaytele_mcp.ai import apply_ai_filter, looks_like_ai
from cosplaytele_mcp.htmlutil import (
    download_urls_from_html,
    host_key,
    looks_like_video,
    normalize_path,
    slugify,
)
from cosplaytele_mcp.models import ListingItem, ListingPage, SearchHit, needed_images, window_images
from cosplaytele_mcp.server import interleave_hits
from cosplaytele_mcp.sources import SourceError, SourceRegistry, source_allowed_hosts
from cosplaytele_mcp.sources.baobua import BaoBuaSource
from cosplaytele_mcp.sources.buondua import BuonDuaSource
from cosplaytele_mcp.sources.cosplaytele import CATEGORIES, CosplayTeleSource
from cosplaytele_mcp.sources.hentaicosplay import HentaiCosplaySource
from cosplaytele_mcp.sources.jjcos import JJCOSSource, posts_from_index
from cosplaytele_mcp.sources.kiutaku import KiutakuSource
from cosplaytele_mcp.sources.lovecutes import LoveCutesSource
from cosplaytele_mcp.sources.ososedki import OsosedkiSource
from cosplaytele_mcp.sources.simplycosplay import SimplyCosplaySource, token_from_script
from cosplaytele_mcp.sources.xasiat import XasiatSource, looks_like_cosplay
from cosplaytele_mcp.wordpress import listing_from_posts

GALLERY_HTML = """
<html><body>
<h1 class="entry-title">Kisaki Gallery 112 photos and 1 video</h1>
<time class="updated" datetime="2026-09-09T18:22:18+08:00"></time>
<div id="main">
  <a href="https://cosplaytele.com/category/cosplay/">Cosplay</a>
  <a href="https://cosplaytele.com/tag/blue-archive/">Blue Archive</a>
  <a href="https://gofile.io/d/2zFOOMkn">Download</a>
</div>
<div class="gallery">
  <figure class="gallery-item"><img src="https://cosplaytele.com/wp-content/uploads/a.webp"></figure>
  <figure class="gallery-item"><img src="https://cosplaytele.com/wp-content/uploads/b.webp"></figure>
</div>
<iframe src="https://cossora.stream/embed/c52eff92-b996-4c30-9f54-1c7ddf5b3a57"></iframe>
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


def test_normalize_path_rejects_disguised_absolute_url() -> None:
    with pytest.raises(ValueError, match="absolute URL"):
        normalize_path("/http://127.0.0.1:8080/demo", "https://cosplaytele.com")


def test_normalize_path_accepts_language_subdomain() -> None:
    base = "https://hentai-cosplay-xxx.com"
    assert normalize_path("https://ja.hentai-cosplay-xxx.com/image/aqua/", base) == "/image/aqua/"


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
    html = gallery.html or ""
    assert download_urls_from_html(html) == ["https://gofile.io/d/2zFOOMkn"]
    assert looks_like_video(title="Kisaki Gallery 112 photos and 1 video", html=html)
    window = source.make_gallery(
        title="Kisaki Gallery",
        path="/kisaki/",
        url="https://cosplaytele.com/kisaki/",
        images=images,
        offset=0,
        limit=1,
        tags=source._tags(gallery),
    )
    assert window.image_count == 2
    assert window.image_urls == images[:1]
    assert [asset.url for asset in window.image_assets] == images[:1]
    assert window.image_assets[0].headers == {}
    assert window.has_more_images is True
    meta = source.make_gallery(
        title="Kisaki Gallery",
        path="/kisaki/",
        url="https://cosplaytele.com/kisaki/",
        images=images,
        offset=0,
        limit=0,
    )
    assert meta.image_urls == []
    assert meta.image_assets == []
    assert meta.image_count == 2


def test_hentaicosplay_image_assets_include_hotlink_headers() -> None:
    source = HentaiCosplaySource(http=None)  # type: ignore[arg-type]
    gallery = source.make_gallery(
        title="Probe",
        path="/image/probe/",
        url="https://hentai-cosplay-xxx.com/image/probe/",
        images=["https://static17.hentai-cosplay-xxx.com/upload/probe.webp"],
    )
    assert gallery.image_assets[0].headers["Referer"] == "https://hentai-cosplay-xxx.com/"
    assert "Mozilla/5.0" in gallery.image_assets[0].headers["User-Agent"]


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


def test_cosplaytele_known_categories() -> None:
    assert "nude" not in CATEGORIES
    assert "no-nude" not in CATEGORIES
    assert "video-cosplay" in CATEGORIES
    assert "free-style" in CATEGORIES


def test_hentaicosplay_ranking_url() -> None:
    source = HentaiCosplaySource(http=None)  # type: ignore[arg-type]
    assert source._ranking_url(1, None, None).endswith("/ranking/page/1/")
    assert source._ranking_url(2, "like", "day").endswith("/ranking-like/type/day/page/2/")
    with pytest.raises(SourceError, match="period"):
        source._ranking_url(1, "like", "last7days")


def test_cosplaytele_wp_search_item() -> None:
    source = CosplayTeleSource(http=None)  # type: ignore[arg-type]
    item = source._from_wp_post(
        {
            "title": {"rendered": "Yaokoututu cosplay Ryuuge Kisaki &#8211; Blue Archive"},
            "link": "https://cosplaytele.com/ryuuge-kisaki-4/",
            "date": "2026-09-09T18:22:18",
            "_embedded": {
                "wp:featuredmedia": [{"source_url": "https://cosplaytele.com/cover.webp"}],
                "wp:term": [[{"name": "Blue Archive", "slug": "blue-archive"}]],
            },
        }
    )
    assert item.path == "/ryuuge-kisaki-4/"
    assert "Kisaki" in item.title
    assert "–" in item.title or "-" in item.title
    assert item.thumbnail_url.endswith("cover.webp")
    assert item.tags == ["Blue Archive"]
    assert item.published_at == "2026-09-09"


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
    assert item.published_at == "2026-09-08"


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


def test_window_images() -> None:
    urls = ["a", "b", "c"]
    assert window_images(urls, offset=0, limit=2) == (["a", "b"], 3, 0, True)
    assert window_images(urls, offset=0, limit=0) == ([], 3, 0, True)
    assert window_images(urls, offset=0, limit=None) == (["a", "b", "c"], 3, 0, False)
    assert window_images(urls, offset=2, limit=2) == (["c"], 3, 2, False)
    assert window_images(
        ["a", "b"],
        offset=0,
        limit=20,
        complete=False,
        has_more=True,
    ) == (["a", "b"], None, 0, True)
    assert window_images(
        ["a", "b"],
        offset=0,
        limit=20,
        complete=False,
        has_more=False,
    ) == (["a", "b"], 2, 0, False)
    assert needed_images(10, 20) == 30
    assert needed_images(0, 0) == 0
    assert needed_images(5, None) is None


def test_host_key_and_slugify() -> None:
    assert host_key("https://www.4khd.com/post") == "4khd.com"
    assert host_key("https://cosplaytele.com/x/") == "cosplaytele.com"
    assert slugify("Blue Archive") == "blue-archive"
    assert slugify("  Kisaki ") == "kisaki"


def test_registry_by_url() -> None:
    registry = SourceRegistry(None)  # type: ignore[arg-type]
    assert registry.by_url("https://cosplaytele.com/ryuuge-kisaki-4/").id == "cosplaytele"
    assert registry.by_url("https://www.4khd.com/foo.html").id == "fourkhd"
    assert registry.by_url("https://ja.hentai-cosplay-xxx.com/image/aqua/").id == "hentaicosplay"
    assert registry.by_url("https://www.lovecutes.com/article/32567/").id == "lovecutes"
    assert registry.by_url("https://www.simply-cosplay.com/gallery/new/eula/").id == "simplycosplay"
    assert registry.by_url("https://jjcos.com/post/Eula/").id == "jjcos"
    assert registry.by_url("https://buondua.com/tag/cosplay-10688").id == "buondua"
    assert registry.by_url("https://www.xasiat.com/albums/1/eula/").id == "xasiat"
    assert registry.by_url("https://baobua.net/spot/abc.html").id == "baobua"
    with pytest.raises(SourceError, match="No source for host"):
        registry.by_url("https://example.com/x")


def test_source_allowed_hosts_includes_simply_cosplay_api() -> None:
    hosts = {host_key(item) for item in source_allowed_hosts()}
    assert "simply-cosplay.com" in hosts
    assert "api.simply-porn.com" in hosts


def test_download_urls_and_video_hints() -> None:
    html = """
    <a href="https://gofile.io/d/abc">zip</a>
    <a href="https://t.me/skip">tg</a>
    <iframe src="https://cossora.stream/embed/uuid"></iframe>
    """
    assert download_urls_from_html(html) == ["https://gofile.io/d/abc"]
    assert looks_like_video(title="Kisaki 112 photos and 1 video")
    assert looks_like_video(html=html)
    assert not looks_like_video(title="Kisaki 112 photos")


def test_interleave_hits() -> None:
    def hit(source: str, title: str) -> SearchHit:
        return SearchHit(
            source=source,  # type: ignore[arg-type]
            title=title,
            path=f"/{title}/",
            url=f"https://example.com/{title}/",
        )

    items = interleave_hits(
        [
            [hit("cosplaytele", "a1"), hit("cosplaytele", "a2")],
            [hit("everia", "b1")],
            [hit("cosplaytele", "a1")],
        ]
    )
    assert [item.title for item in items] == ["a1", "b1", "a2"]


def test_listing_from_posts_includes_tags_and_date() -> None:
    page = listing_from_posts(
        "cup2d",
        1,
        [
            {
                "title": {"rendered": "Kisaki"},
                "link": "https://cup2d.com/kisaki/",
                "date": "2026-09-09T12:00:00",
                "content": {
                    "rendered": '<img src="https://cup2d.com/a.webp"><img src="https://cup2d.com/b.webp">'
                },
                "_embedded": {
                    "wp:term": [[{"name": "Blue Archive"}, {"name": "cosplay"}]],
                    "wp:featuredmedia": [{"source_url": "https://cup2d.com/cover.webp"}],
                },
            }
        ],
        False,
    )
    item = page.items[0]
    assert item.tags == ["Blue Archive", "cosplay"]
    assert item.published_at == "2026-09-09"
    assert item.image_count == 2
    assert item.thumbnail_url == "https://cup2d.com/cover.webp"
    assert item.has_video is False


def test_apply_ai_filter_uses_listing_tags() -> None:
    page = ListingPage(
        source="hentaicosplay",
        page=1,
        has_next_page=False,
        items=[
            ListingItem(
                title="Aqua",
                path="/image/aqua/",
                url="https://hentai-cosplay-xxx.com/image/aqua/",
                tags=["ai-generated"],
            )
        ],
    )
    filtered = apply_ai_filter(page, exclude_ai=True)
    assert filtered.items == []


def test_lovecutes_listing_item_uses_original_thumbnail_and_cosplay_marker() -> None:
    source = LoveCutesSource(http=None)  # type: ignore[arg-type]
    node = HTMLParser(
        """
        <article class="excerpt excerpt-c4">
          <a class="imgbox-a" href="/type/6/">Cosplay</a>
          <a class="imgbox-link" href="/article/32567/" title="[cosplay] Kisaki 27P"></a>
          <img class="imgbox-img" src="/static/zde/timg.gif"
               data-original-src="/static/images/cover.jpg">
          <footer><time>2026-09-09</time></footer>
        </article>
        """
    ).css_first("article")
    item = source._listing_item(node, require_cosplay=True)
    assert item is not None
    assert item.path == "/article/32567/"
    assert item.thumbnail_url == "https://www.lovecutes.com/static/images/cover.jpg"
    assert item.image_count == 27
    assert item.published_at == "2026-09-09"


def test_lovecutes_listing_excludes_non_cosplay_search_results() -> None:
    source = LoveCutesSource(http=None)  # type: ignore[arg-type]
    node = HTMLParser(
        '<article class="excerpt"><a class="imgbox-link" href="/article/1/" title="Other"></a></article>'
    ).css_first("article")
    assert source._listing_item(node, require_cosplay=True) is None


def test_lovecutes_article_pagination_and_image_window() -> None:
    source = LoveCutesSource(http=None)  # type: ignore[arg-type]
    html = """
      <h1 class="focusbox-title">Kisaki 27P</h1>
      <script>const paginationData = {"current_page": 1, "total_pages": 3};</script>
      <div class="image-container">
        <img class="item-image__img" src="/static/images/001.jpg">
        <img class="item-image__img" src="/static/zde/timg.gif" data-src="/static/images/002.jpg">
      </div>
    """
    document = HTMLParser(html)
    assert source._total_pages(html) == 3
    assert source._known_count("Kisaki 27P", html) == 27
    assert source._page_images(document) == [
        "https://www.lovecutes.com/static/images/001.jpg",
        "https://www.lovecutes.com/static/images/002.jpg",
    ]


def test_simplycosplay_token_and_listing() -> None:
    assert token_from_script('foo token:"01730876" bar') == "01730876"
    assert token_from_script("token: 'abc123'") == "abc123"
    source = SimplyCosplaySource(http=None)  # type: ignore[arg-type]
    items = source._listing_items(
        {
            "data": [
                {
                    "title": "Eula",
                    "slug": "eula-set",
                    "type": "Gallery",
                    "preview": {
                        "publish_date": "2026-09-01T12:00:00.000",
                        "urls": {"thumb": {"url": "https://cdn.example/thumb.webp"}},
                    },
                }
            ]
        }
    )
    assert items[0].path == "/gallery/new/eula-set"
    assert items[0].thumbnail_url == "https://cdn.example/thumb.webp"
    assert items[0].published_at == "2026-09-01"
    images = source._gallery_images(
        {
            "images": [
                {"urls": {"url": "https://cdn.example/1.webp"}},
                {"urls": {"url": "https://cdn.example/2.webp"}},
            ],
            "preview": {"urls": {"url": "https://cdn.example/preview.webp"}},
        }
    )
    assert images == ["https://cdn.example/1.webp", "https://cdn.example/2.webp"]
    assert source._kind_and_slug("/gallery/new/eula-set") == ("gallery", "eula-set")


def test_jjcos_listing_and_gallery_images() -> None:
    source = JJCOSSource(http=None)  # type: ignore[arg-type]
    document = HTMLParser(
        """
        <article class="custom-article">
          <figure class="img-box">
            <a href="https://jjcos.com/post/Cosplay Eula/">
              <img src="https://i1.wp.com/example/cover.webp">
            </a>
            <span class="breadcrumb-item date-overlay">2025-12-26</span>
          </figure>
          <a href="https://jjcos.com/tag/HSQ2151O0wZ/" class="tag"> #Cosplay </a>
          <h3 class="fh5co-article-title">
            <a href="https://jjcos.com/post/Cosplay Eula/">Cosplay Eula</a>
          </h3>
        </article>
        """
    )
    item = source.listing_items(document)[0]
    assert item.path == "/post/Cosplay%20Eula/"
    assert item.tags == ["Cosplay"]
    assert item.published_at == "2025-12-26"
    gallery = HTMLParser(
        """
        <h1 class="fh5co-article-title">Cosplay Eula - JJCOS</h1>
        <div id="post-content">
          <img src="https://i1.wp.com/example/1.webp">
          <img src="https://i1.wp.com/example/2.webp">
        </div>
        <img src="https://jjcos.com/images/avatar.png">
        """
    )
    assert source._page_images(gallery) == [
        "https://i1.wp.com/example/1.webp",
        "https://i1.wp.com/example/2.webp",
    ]


def test_xasiat_listing_and_get_image_urls() -> None:
    source = XasiatSource(http=None)  # type: ignore[arg-type]
    document = HTMLParser(
        """
        <div class="list-albums">
          <div class="item">
            <a href="https://www.xasiat.com/albums/37440/eula/" title="[Cosplay] Eula [69P24V]">
              <img class="thumb lazy-load" data-original="https://pic.example/preview.jpg">
              <strong class="title">[Cosplay] Eula [69P24V]</strong>
              <div class="photos">69 photos</div>
            </a>
          </div>
        </div>
        """
    )
    item = source.listing_items(document)[0]
    assert item.path == "/albums/37440/eula/"
    assert item.image_count == 69
    assert item.has_video is True
    gallery = HTMLParser(
        """
        <a href="https://www.xasiat.com/get_image/2/abc/sources/1.jpg/?i-acctoken=tok" class="item">
          <img class="thumb">
        </a>
        <a href="/albums/categories/cosplay/">Cosplay</a>
        """
    )
    assert source._page_images(gallery) == [
        "https://www.xasiat.com/get_image/2/abc/sources/1.jpg/?i-acctoken=tok"
    ]


def test_baobua_listing_and_fullsize_images() -> None:
    source = BaoBuaSource(http=None)  # type: ignore[arg-type]
    document = HTMLParser(
        """
        <link rel="next" href="https://baobua.net/category/Cosplay?page=2">
        <div class="thumb-view">
          <a href="/spot/abc.html" title="Sexy Dust Hunter">
            <img class="play_img" src="/privid2/play_m.png">
            <img class="xld" src="https://blogger.googleusercontent.com/img/s320/cover.jpg">
          </a>
        </div>
        """
    )
    item = source.listing_items(document)[0]
    assert item.path == "/spot/abc.html"
    assert item.title == "Sexy Dust Hunter"
    gallery = HTMLParser(
        """
        <title>BaoBua.Net: Sexy Dust Hunter | Page 1/2</title>
        <img src="https://blogger.googleusercontent.com/img/b/xxx/s0/Horny_Maid%20(1).jpg">
        <img src="https://blogger.googleusercontent.com/img/b/xxx/s320/sidebar.jpg">
        <script>var initRelated= { tag: ["Cosplay","maid"] };</script>
        <a class="page-numbers" href="/spot/abc.html?page=2">Next ></a>
        """
    )
    assert source._title(gallery) == "Sexy Dust Hunter"
    assert source._page_images(gallery) == [
        "https://blogger.googleusercontent.com/img/b/xxx/s0/Horny_Maid%20(1).jpg"
    ]
    assert source._tags(gallery.html or "") == ["Cosplay", "maid"]
    assert source._next_page(gallery, "https://baobua.net/spot/abc.html") == (
        "https://baobua.net/spot/abc.html?page=2"
    )


def test_buondua_defaults_to_the_cosplay_tag() -> None:
    source = BuonDuaSource(http=None)  # type: ignore[arg-type]
    assert source.id == "buondua"
    assert source.supports_latest is False
    assert source._tag_url("cosplay-10688", 1) == "https://buondua.com/tag/cosplay-10688"
    assert source._tag_url("cosplay-10688", 2) == "https://buondua.com/tag/cosplay-10688?start=20"


def test_simplycosplay_rejects_unknown_gallery_kinds() -> None:
    source = SimplyCosplaySource(http=None)  # type: ignore[arg-type]
    items = source._listing_items({"data": [{"title": "leak", "slug": "secret", "type": "user"}]})
    assert items == []
    with pytest.raises(SourceError, match="not a gallery"):
        source._kind_and_slug("/user/secret")
    with pytest.raises(SourceError, match="not a gallery"):
        source._kind_and_slug("/v2/admin/token")


def test_jjcos_index_requires_posts_and_matches_tags() -> None:
    with pytest.raises(SourceError, match="missing posts"):
        posts_from_index({"status": "ok"})
    with pytest.raises(SourceError, match="not a list"):
        posts_from_index({"posts": {"title": "nope"}})
    posts = posts_from_index(
        {
            "posts": [
                {"title": "Portrait", "link": "/post/portrait/", "tags": ["Cosplay"]},
                {"title": "Selfie", "link": "/post/selfie/", "tags": ["gravure"]},
                {"title": "Eula Cosplay", "link": "/post/eula/"},
            ]
        }
    )
    assert [post["title"] for post in posts] == ["Portrait", "Eula Cosplay"]
    assert "cosplay" in posts[0]["haystack"]


def test_xasiat_search_category_filters_cosplay() -> None:
    source = XasiatSource(http=None)  # type: ignore[arg-type]
    assert looks_like_cosplay("[Cosplay] Eula", "/albums/1/eula/")
    assert not looks_like_cosplay("Gravure set", "/albums/2/gravure/")
    assert source._require_search_category(None) is None
    assert source._require_search_category("Cosplay") == "cosplay"
    with pytest.raises(SourceError, match="only supports the cosplay category"):
        source._require_search_category("gravure")


def test_baobua_search_stays_on_the_site_query_string() -> None:
    source = BaoBuaSource(http=None)  # type: ignore[arg-type]
    assert source._search_url("eula", 1) == "https://baobua.net/?s=eula"
    assert source._search_url("eula", 2) == "https://baobua.net/?s=eula&page=2"


def test_buondua_parses_listing_and_gallery_html() -> None:
    listing_html = """
    <div class="items-row">
      <a class="item-link" href="/eula-cosplay-1/">
        <img src="https://buondua.com/thumb.webp">
        <h2>Eula Cosplay</h2>
      </a>
    </div>
    """
    kiutaku = KiutakuSource(http=None)  # type: ignore[arg-type]
    buondua = BuonDuaSource(http=None)  # type: ignore[arg-type]
    document = HTMLParser(listing_html)
    assert kiutaku.listing_items(document) == []
    item = buondua.listing_items(document)[0]
    assert item.path == "/eula-cosplay-1/"
    assert item.title == "Eula Cosplay"
    assert item.thumbnail_url == "https://buondua.com/thumb.webp"
    gallery = HTMLParser(
        """
        <div class="article-header">Eula Cosplay - (Page 1 / 2)</div>
        <div class="article-fulltext">
          <img src="https://buondua.com/1.webp">
          <img src="https://buondua.com/2.webp">
        </div>
        """
    )
    assert buondua._gallery_title(gallery) == "Eula Cosplay"
    assert buondua._page_images(gallery) == [
        "https://buondua.com/1.webp",
        "https://buondua.com/2.webp",
    ]
