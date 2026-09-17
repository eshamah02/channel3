"""Proves the variant design with zero LLM calls.

Inputs are hand-built to shapes seen generically across commerce pages (a
single-axis footwear matrix, a sparse two-axis apparel matrix). No markup is
copied out of data/ -- these are structural cases, not site fixtures.
"""

import pytest
from pydantic import ValidationError

from extraction.derive import (
    content_hash,
    derive_colors,
    derive_options,
    make_id,
    make_slug,
)
from models import (
    Category,
    ExtractedProduct,
    ExtractionMetadata,
    Price,
    Product,
    Variant,
    VariantAttribute,
)


def variant(**axes: str) -> Variant:
    """Build a variant from axis=value kwargs, preserving argument order."""
    return Variant(
        option_values=[VariantAttribute(name=k, value=v) for k, v in axes.items()]
    )


# --- derive_options ---------------------------------------------------------


def test_single_axis_many_variants_yields_one_option_in_page_order():
    """A footwear-style matrix: one axis, many values, semantic ordering.

    Sizes are supplied in the order a page lists them. Sorting would give
    10, 10.5, 11, 6, 7 -- correct alphabetically, useless as a picker.
    """
    page_order = ["6", "6.5", "7", "8", "9", "10", "10.5", "11", "12", "13"]
    variants = [variant(Size=size) for size in page_order]

    options = derive_options(variants)

    assert len(options) == 1
    assert options[0].name == "Size"
    assert options[0].values == page_order


def test_repeated_values_are_deduped():
    variants = [variant(Size="M"), variant(Size="M"), variant(Size="L")]
    assert derive_options(variants)[0].values == ["M", "L"]


def test_sparse_matrix_yields_union_per_axis():
    """Black/S, Black/M, White/S -- White/M is never offered.

    The axes must still advertise both colours and both sizes; the missing
    combination is a stock fact, not an axis fact.
    """
    variants = [
        variant(Color="Black", Size="S"),
        variant(Color="Black", Size="M"),
        variant(Color="White", Size="S"),
    ]

    options = {o.name: o.values for o in derive_options(variants)}

    assert options == {"Color": ["Black", "White"], "Size": ["S", "M"]}


def test_axis_order_follows_first_appearance():
    variants = [variant(Size="S", Color="Black")]
    assert [o.name for o in derive_options(variants)] == ["Size", "Color"]


def test_empty_and_blank_inputs():
    assert derive_options([]) == []
    # A blank value carries no selectable choice, so the axis is dropped rather
    # than rendering an empty picker entry.
    assert derive_options([variant(Size="  ")]) == []
    assert derive_options([Variant(option_values=[])]) == []


def test_case_and_whitespace_variations_collapse():
    variants = [variant(Color="Black"), variant(Color="black "), variant(Color="BLACK")]
    # First spelling seen survives.
    assert derive_options(variants)[0].values == ["Black"]


# --- derive_colors ---------------------------------------------------------


def test_derive_colors_prefers_the_model_list():
    """Prose colour names ("Heather Grey") often aren't variant axis values."""
    variants = [variant(Color="Black")]
    assert derive_colors(variants, ["Heather Grey", "Navy"]) == ["Heather Grey", "Navy"]


def test_derive_colors_falls_back_to_a_colour_axis():
    variants = [variant(Color="Black", Size="S"), variant(Color="White", Size="S")]
    assert derive_colors(variants, []) == ["Black", "White"]
    assert derive_colors(variants, None) == ["Black", "White"]


def test_derive_colors_accepts_en_gb_spelling():
    assert derive_colors([variant(Colour="Ecru")], []) == ["Ecru"]


def test_derive_colors_is_empty_when_neither_source_exists():
    assert derive_colors([variant(Size="S")], []) == []
    assert derive_colors([], []) == []


def test_derive_colors_ignores_non_colour_axes():
    """A drill has a Voltage axis and no colours; inventing one would be wrong."""
    assert derive_colors([variant(Voltage="20V")], []) == []


# --- identity --------------------------------------------------------------


def test_make_id_is_stable_and_short():
    url = "https://example.com/products/widget"
    assert make_id(url) == make_id(url)
    assert len(make_id(url)) == 16


def test_make_id_distinguishes_urls():
    urls = [
        "https://example.com/products/a",
        "https://example.com/products/b",
        "https://example.com/products/a?variant=2",
        "https://other.example/products/a",
    ]
    assert len({make_id(u) for u in urls}) == len(urls)


def test_make_id_ignores_surrounding_whitespace():
    """canonical hrefs are frequently indented inside the <head>."""
    assert make_id("  https://example.com/p  ") == make_id("https://example.com/p")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Simple Name", "simple-name"),
        ("Men's Air-Max 90 (Black/White)", "men-s-air-max-90-black-white"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("Multiple   Spaces", "multiple-spaces"),
        ("Café Crème", "cafe-creme"),  # accents folded to base letters
        ("100% Cotton — Slim Fit", "100-cotton-slim-fit"),
        ("", "product"),  # never return an empty path fragment
        ("!!!", "product"),
    ],
)
def test_make_slug(name, expected):
    assert make_slug(name) == expected


def test_make_slug_keeps_non_latin_scripts():
    """Folding CJK away would collapse every such name to the same slug."""
    slug = make_slug("純綿 T-Shirt")
    assert slug != "product"
    assert "t-shirt" in slug


def test_make_slug_bounds_length_at_a_word_boundary():
    name = " ".join(["extremely"] * 40)
    slug = make_slug(name)
    assert len(slug) <= 80
    assert not slug.endswith("-")
    # Truncation lands between words, not mid-word.
    assert all(part == "extremely" for part in slug.split("-"))


def test_content_hash_is_sensitive_to_any_change():
    html = "<html><body>hello</body></html>"
    assert content_hash(html) == content_hash(html)
    assert content_hash(html) != content_hash(html.replace("hello", "hell0"))
    assert len(content_hash(html)) == 64


def test_content_hash_survives_undecodable_input():
    """One malformed page must not abort a batch."""
    assert len(content_hash("valid \ud800 surrogate")) == 64


# --- the envelope ----------------------------------------------------------


def sample_product(**overrides) -> Product:
    defaults = dict(
        name="Test Widget",
        price=Price(price=29.99, currency="USD"),
        description="A widget for testing.",
        key_features=["Durable", "Washable"],
        image_urls=["https://example.com/a.jpg"],
        category=Category(name="Apparel & Accessories > Clothing"),
        brand="Example Brand",
        colors=["Black"],
        variants=[variant(Color="Black", Size="S"), variant(Color="Black", Size="M")],
    )
    return Product(**{**defaults, **overrides})


def test_envelope_assembles_by_hand():
    """The task-2 'done when': build one by hand, read derived axes off it."""
    product = sample_product()
    url = "https://example.com/products/test-widget"

    envelope = ExtractedProduct(
        id=make_id(url),
        slug=make_slug(product.name),
        source_url=url,
        source_file="data/example.html",
        content_hash=content_hash("<html></html>"),
        options=derive_options(product.variants),
        product=product,
        extraction=ExtractionMetadata(field_tiers={"price.price": "A"}, cost_usd=0.0004),
    )

    assert envelope.slug == "test-widget"
    assert {o.name: o.values for o in envelope.options} == {
        "Color": ["Black"],
        "Size": ["S", "M"],
    }
    # Round-trips, which is what the ingest CLI and the API depend on.
    assert ExtractedProduct.model_validate_json(envelope.model_dump_json()) == envelope


def test_envelope_defaults_are_not_shared_between_instances():
    """Each instance gets its own collections.

    Pydantic copies mutable defaults, so default_factory isn't strictly required
    for this -- it's used in models.py for explicitness. This pins the behaviour
    the ingest CLI relies on: warnings accumulated on one product must not show
    up on the next.
    """
    first = ExtractedProduct(
        id="a", slug="a", source_file="a.html", content_hash="x", product=sample_product()
    )
    second = ExtractedProduct(
        id="b", slug="b", source_file="b.html", content_hash="y", product=sample_product()
    )
    first.extraction.warnings.append("leaked?")
    assert second.extraction.warnings == []


def test_variants_are_typed_not_list_any():
    """The TODO in their schema asked for this; a shapeless variant must fail."""
    with pytest.raises(ValidationError):
        sample_product(variants=[{"unexpected": "shape"}])

    # Valid variant dicts still coerce, so JSON round-trips work.
    coerced = sample_product(
        variants=[{"option_values": [{"name": "Size", "value": "S"}]}]
    )
    assert coerced.variants[0].option_values[0].name == "Size"


def test_variant_carries_optional_per_sku_detail():
    """Some pages publish a price and stock per SKU; most publish neither."""
    rich = Variant(
        option_values=[VariantAttribute(name="Size", value="M")],
        sku="ABC-123",
        price=Price(price=19.99, currency="GBP", compare_at_price=29.99),
        in_stock=False,
        image_urls=["https://example.com/m.jpg"],
    )
    assert rich.price.compare_at_price == 29.99
    assert rich.in_stock is False

    sparse = Variant(option_values=[VariantAttribute(name="Size", value="M")])
    assert sparse.sku is None and sparse.in_stock is None and sparse.image_urls == []
