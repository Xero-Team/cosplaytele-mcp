from cosplaytele_mcp.adult_labels import apply_adult_labels, label_adult_theme_text, label_gallery
from cosplaytele_mcp.models import Gallery, ListingItem, ListingPage


def test_label_adult_theme_text_marks_age_coded_costume_terms() -> None:
    assert (
        label_adult_theme_text("JK制服 / School Girl / After School / Loli")
        == "JK (18+) 制服 (18+) / School Girl (18+) / After School (18+) / Loli (18+)"
    )


def test_label_adult_theme_text_is_idempotent() -> None:
    text = "JK (18+) 制服 (18+) / school uniform (18+)"
    assert label_adult_theme_text(text) == text


def test_apply_adult_labels_updates_listing_titles_and_tags() -> None:
    page = ListingPage(
        source="cosplaytele",
        page=1,
        has_next_page=False,
        items=[
            ListingItem(
                title="风纪委员死库水",
                path="/jk/",
                url="https://example.com/jk/",
                tags=["school uniform", "cosplay"],
            )
        ],
    )

    labeled = apply_adult_labels(page).items[0]

    assert labeled.title == "风纪委员 (18+) 死库水 (18+)"
    assert labeled.tags == ["school uniform (18+)", "cosplay"]


def test_label_gallery_preserves_source_identifiers() -> None:
    gallery = Gallery(
        source="cosplaytele",
        title="美少女 JK",
        path="/source-title/",
        url="https://example.com/source-title/",
        image_urls=[],
    )

    labeled = label_gallery(gallery)

    assert labeled.title == "美少女 (18+) JK (18+)"
    assert labeled.path == "/source-title/"
    assert labeled.url == "https://example.com/source-title/"
