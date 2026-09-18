"""The tier-A/B extraction layers, against hand-written spec fixtures.

Every fixture is written from the schema.org / OpenGraph / HTML-microdata specs.
None is copied out of data/: a suite seeded from the provided pages would pass
while the code was quietly tuned to those pages, which is the exact failure the
brief disqualifies, and it would make these tests worthless as evidence that the
layers generalise.
"""

from pathlib import Path

import pytest

from extraction.layers import jsonld, meta, microdata
from extraction.candidates import (
    BRAND,
    COLORS,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    NAME,
    PRICE,
    VARIANTS,
    VIDEO_URL,
    CandidateBundle,
    Tier,
)
from extraction.derive import derive_options
from extraction.dom import canonical_url, parse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return parse((FIXTURES / name).read_text())


def bundle_from(name: str, *layers) -> CandidateBundle:
    soup = load(name)
    bundle = CandidateBundle()
    for layer in layers or (jsonld, microdata, meta):
        layer.extract(soup, bundle)
    return bundle


def values(bundle: CandidateBundle, field: str) -> list:
    return [candidate.value for candidate in bundle.for_field(field)]


# --- JSON-LD ---------------------------------------------------------------


def test_jsonld_product_maps_every_field():
    bundle = bundle_from("jsonld_product.html", jsonld)

    assert bundle.best(NAME).value == "Structured Widget"
    assert bundle.best(DESCRIPTION).value == "A widget described in JSON-LD."
    assert bundle.best(PRICE).value == 49.95
    assert bundle.best(CURRENCY).value == "USD"
    assert bundle.best(VIDEO_URL).value == "https://cdn.test/widget.mp4"
    assert bundle.best(NAME).tier is Tier.A


def test_jsonld_brand_object_and_string_agree():
    """brand is a Brand object in one fixture and a bare string in the other."""
    assert bundle_from("jsonld_product.html", jsonld).best(BRAND).value == "Fixture Brand"
    assert bundle_from("jsonld_graph.html", jsonld).best(BRAND).value == "Graph Brand"


def test_jsonld_image_list_and_string_agree():
    listed = bundle_from("jsonld_product.html", jsonld).best(IMAGE_URLS).value
    assert listed == ["https://cdn.test/widget-1.jpg", "https://cdn.test/widget-2.jpg"]

    single = bundle_from("jsonld_graph.html", jsonld).best(IMAGE_URLS).value
    assert single == ["https://cdn.test/graph.jpg"]


def test_jsonld_color_becomes_a_list():
    assert bundle_from("jsonld_product.html", jsonld).best(COLORS).value == ["Slate"]


def test_jsonld_resolves_graph_and_main_entity():
    """The Product sits inside an array, inside @graph, inside a WebPage."""
    bundle = bundle_from("jsonld_graph.html", jsonld)
    assert bundle.best(NAME).value == "Graph Widget"


def test_jsonld_ignores_related_products():
    """isRelatedTo holds another Product; its name and price must not leak in."""
    bundle = bundle_from("jsonld_graph.html", jsonld)
    assert "Unrelated Accessory" not in values(bundle, NAME)
    assert 9.99 not in values(bundle, PRICE)
    assert "EUR" not in values(bundle, CURRENCY)


def test_aggregate_offer_high_price_becomes_compare_at():
    bundle = bundle_from("jsonld_graph.html", jsonld)
    assert bundle.best(PRICE).value == 80.00
    assert bundle.best(COMPARE_AT_PRICE).value == 120.00
    assert bundle.best(CURRENCY).value == "GBP"


def test_high_price_equal_to_price_is_not_a_sale():
    """A range where both ends match is not a discount."""
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Flat","offers":'
        '{"@type":"AggregateOffer","lowPrice":"10.00","highPrice":"10.00","priceCurrency":"USD"}}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert bundle.best(PRICE).value == 10.00
    assert bundle.best(COMPARE_AT_PRICE) is None


def test_malformed_block_does_not_block_the_others():
    """Three blocks, the middle one invalid JSON."""
    bundle = bundle_from("jsonld_malformed.html", jsonld)
    assert bundle.best(NAME).value == "Survivor Widget"
    assert bundle.best(PRICE).value == 12.34
    assert "Broken" not in values(bundle, NAME)


def test_non_product_types_are_ignored():
    """The Organization node in the same document is not the product."""
    bundle = bundle_from("jsonld_malformed.html", jsonld)
    assert "Fixture Org" not in values(bundle, NAME)


def test_productgroup_variants_carry_axes_sku_price_and_stock():
    bundle = bundle_from("jsonld_productgroup.html", jsonld)
    variants = bundle.all_at_best_tier(VARIANTS)
    assert len(variants) == 3

    first = variants[0].value
    # variesBy declares colour before size, so the axis order follows the publisher.
    assert [(a.name, a.value) for a in first.option_values] == [
        ("Color", "Charcoal"),
        ("Size", "S"),
    ]
    assert first.sku == "TEE-CHAR-S"
    assert first.price.price == 32.00
    assert first.price.currency == "USD"
    assert first.in_stock is True
    assert first.image_urls == ["https://cdn.test/tee-charcoal.jpg"]

    assert variants[1].value.in_stock is False  # OutOfStock
    assert variants[2].value.in_stock is None  # availability unstated


def test_variant_additional_property_becomes_an_axis():
    """additionalProperty covers axes the core vocabulary has no term for."""
    bundle = bundle_from("jsonld_productgroup.html", jsonld)
    third = bundle.all_at_best_tier(VARIANTS)[2].value
    assert ("Fit", "Relaxed") in [(a.name, a.value) for a in third.option_values]


def test_variants_feed_task_2_derivation_with_a_sparse_matrix():
    """End-to-end on the piece task 2 built: Sand/M is never offered."""
    bundle = bundle_from("jsonld_productgroup.html", jsonld)
    variants = [candidate.value for candidate in bundle.all_at_best_tier(VARIANTS)]
    axes = {option.name: option.values for option in derive_options(variants)}
    assert axes["Color"] == ["Charcoal", "Sand"]
    assert axes["Size"] == ["S", "M"]


def test_group_without_offers_takes_its_price_from_variants():
    """A ProductGroup may be unpriced and price each SKU instead.

    The group is an abstract item; only the variants are purchasable. Reading
    only group-level offers loses the price entirely on such a page.
    """
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"ProductGroup","name":"Grouped","variesBy":["https://schema.org/size"],'
        '"hasVariant":['
        '{"@type":"Product","size":"S","offers":{"@type":"Offer","price":76.99,"priceCurrency":"GBP"}},'
        '{"@type":"Product","size":"M","offers":{"@type":"Offer","price":76.99,"priceCurrency":"GBP"}}]}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)

    assert bundle.best(PRICE).value == 76.99
    assert bundle.best(CURRENCY).value == "GBP"
    assert "hasVariant" in bundle.best(PRICE).source
    assert bundle.best(COMPARE_AT_PRICE) is None  # uniform pricing is not a sale


def test_variant_price_fallback_reports_the_lowest_and_flags_a_spread():
    """Mixed variant pricing is the "from $X" case a storefront displays."""
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"ProductGroup","name":"Spread","hasVariant":['
        '{"@type":"Product","size":"S","offers":{"@type":"Offer","price":30.00,"priceCurrency":"USD"}},'
        '{"@type":"Product","size":"XXL","offers":{"@type":"Offer","price":38.00,"priceCurrency":"USD"}}]}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert bundle.best(PRICE).value == 30.00
    assert bundle.best(COMPARE_AT_PRICE).value == 38.00


def test_group_offers_win_over_variant_prices():
    """When the group states its own price, that is the authoritative reading."""
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"ProductGroup","name":"Both",'
        '"offers":{"@type":"Offer","price":50.00,"priceCurrency":"USD"},'
        '"hasVariant":[{"@type":"Product","size":"S",'
        '"offers":{"@type":"Offer","price":99.00,"priceCurrency":"USD"}}]}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert values(bundle, PRICE) == [50.00]


def test_placeholder_variant_entries_are_skipped():
    """hasVariant lists sometimes hold bare links with no option values.

    Emitting those as variants would put empty rows in the picker.
    """
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"ProductGroup","name":"Stubs","hasVariant":['
        '{"@type":"Product","size":"S"},'
        '{"url":"https://shop.test/other-colourway"},'
        '{"url":"https://shop.test/third-colourway"}]}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert len(bundle.for_field(VARIANTS)) == 1


def test_hasvariant_entries_are_not_mistaken_for_the_product():
    """Each hasVariant entry is itself a Product node; only the group is the product."""
    bundle = bundle_from("jsonld_productgroup.html", jsonld)
    assert values(bundle, NAME) == ["Grouped Tee"]


def test_jsonld_absent_yields_nothing_rather_than_raising():
    bundle = bundle_from("no_structured_data.html", jsonld)
    assert len(bundle) == 0


def test_empty_and_whitespace_script_is_skipped():
    soup = parse('<script type="application/ld+json">   </script>')
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert len(bundle) == 0


def test_price_currency_from_price_specification():
    """priceSpecification is the long form of a bare price/priceCurrency pair."""
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Spec","offers":{"@type":"Offer",'
        '"priceSpecification":{"@type":"UnitPriceSpecification",'
        '"price":"15.50","priceCurrency":"CHF"}}}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert bundle.best(PRICE).value == 15.50
    assert bundle.best(CURRENCY).value == "CHF"


def test_offers_as_a_list_is_flattened():
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Multi","offers":['
        '{"@type":"Offer","price":"20.00","priceCurrency":"USD"},'
        '{"@type":"Offer","price":"25.00","priceCurrency":"USD"}]}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert values(bundle, PRICE) == [20.00, 25.00]


def test_type_may_be_a_list_or_a_full_url():
    soup = parse(
        '<script type="application/ld+json">'
        '{"@type":["https://schema.org/Product","Thing"],"name":"Listed Type"}'
        "</script>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert bundle.best(NAME).value == "Listed Type"


# --- microdata -------------------------------------------------------------


def test_microdata_maps_product_and_offer_properties():
    bundle = bundle_from("microdata_product.html", microdata)

    assert bundle.best(NAME).value == "Microdata Widget"
    assert bundle.best(DESCRIPTION).value == "Described entirely with attributes."
    assert bundle.best(PRICE).value == 74.50
    assert bundle.best(CURRENCY).value == "USD"
    assert bundle.best(NAME).tier is Tier.A


def test_microdata_nested_brand_name_becomes_brand_not_name():
    """<span itemprop="name"> inside a Brand item is the brand's name.

    A flat itemprop table would read it as the product name.
    """
    bundle = bundle_from("microdata_product.html", microdata)
    assert bundle.best(BRAND).value == "Attribute Brand"
    assert "Attribute Brand" not in values(bundle, NAME)


def test_microdata_value_resolution_per_tag():
    """meta -> content, img -> src, and a content attribute overriding text."""
    bundle = bundle_from("microdata_product.html", microdata)
    assert bundle.best(COLORS).value == "Olive"  # from meta content
    assert values(bundle, IMAGE_URLS) == [
        "https://cdn.test/micro-1.jpg",
        "https://cdn.test/micro-2.jpg",
    ]
    # price came from content="74.50", not the "$74.50" text node.
    assert bundle.best(PRICE).value == 74.50


def test_microdata_richest_product_scope_wins():
    """A related-items rail also declares itemtype=Product."""
    bundle = bundle_from("microdata_product.html", microdata)
    assert bundle.best(NAME).value == "Microdata Widget"
    assert "Related Trinket" not in [bundle.best(NAME).value]


def test_microdata_bare_itemprop_price_is_collected():
    """Unscoped itemprops are invalid per spec and ubiquitous in practice."""
    bundle = bundle_from("microdata_bare_price.html", microdata)
    assert bundle.best(PRICE).value == 18.99
    assert bundle.best(COMPARE_AT_PRICE).value == 24.99
    assert "unscoped" in bundle.best(PRICE).source


def test_microdata_records_conflicting_currency_declarations():
    """Three priceCurrency metas, two distinct values: resolve and say so."""
    bundle = bundle_from("microdata_bare_price.html", microdata)
    currency = bundle.best(CURRENCY)
    assert currency.value == "GBP"  # first in document order
    assert "conflict: 2 distinct values" in currency.source


def test_microdata_absent_yields_nothing():
    bundle = bundle_from("no_structured_data.html", microdata)
    assert len(bundle) == 0


def test_microdata_does_not_read_unscoped_name():
    """An unscoped itemprop="name" could be any heading on the page."""
    soup = parse('<h2 itemprop="name">Newsletter Signup</h2>')
    bundle = CandidateBundle()
    microdata.extract(soup, bundle)
    assert len(bundle) == 0


def test_microdata_multi_token_itemprop():
    """itemprop is a space-separated token list per the spec."""
    soup = parse(
        '<div itemscope itemtype="https://schema.org/Product">'
        '<meta itemprop="name headline" content="Two Tokens">'
        "</div>"
    )
    bundle = CandidateBundle()
    microdata.extract(soup, bundle)
    assert bundle.best(NAME).value == "Two Tokens"


# --- meta / OpenGraph ------------------------------------------------------


def test_meta_maps_opengraph_and_product_extension():
    bundle = bundle_from("meta_only.html", meta)

    assert bundle.best(PRICE).value == 59.00
    assert bundle.best(CURRENCY).value == "AUD"
    assert bundle.best(COMPARE_AT_PRICE).value == 79.00
    assert bundle.best(DESCRIPTION).value == "A widget described only in metadata."
    assert bundle.best(PRICE).tier is Tier.B


def test_meta_title_carries_store_furniture():
    """og:title contains the name rather than being it; trimming is a judgement."""
    bundle = bundle_from("meta_only.html", meta)
    names = values(bundle, NAME)
    assert "Meta Widget | Free Shipping | Example Store" in names
    assert "Meta Widget" in names  # twitter:title is cleaner


def test_meta_reads_both_property_and_name_attributes():
    """OpenGraph specifies property=, Twitter name=, publishers mix them."""
    bundle = bundle_from("meta_only.html", meta)
    sources = {candidate.source for candidate in bundle.for_field(IMAGE_URLS)}
    assert any("og:image" in source for source in sources)
    assert any("twitter:image" in source for source in sources)


def test_site_name_is_demoted_below_a_real_brand_signal():
    """The trap: on a retailer page og:site_name is the shop, not the maker."""
    bundle = bundle_from("meta_only.html", meta)
    brands = {candidate.value: candidate.tier for candidate in bundle.for_field(BRAND)}
    assert brands["Example Store"] is Tier.D
    assert brands["Meta Maker"] is Tier.B
    assert bundle.best(BRAND).value == "Meta Maker"


def test_title_is_emitted_when_nothing_else_exists():
    bundle = bundle_from("no_structured_data.html", meta)
    assert bundle.best(NAME).value == "Plain Widget"
    assert bundle.fields() == [NAME]


def test_canonical_url_prefers_link_rel_canonical():
    assert canonical_url(load("meta_only.html")) == "https://shop.test/products/meta-widget"


def test_canonical_url_falls_back_to_og_url():
    soup = parse('<meta property="og:url" content="https://shop.test/p/x">')
    assert canonical_url(soup) == "https://shop.test/p/x"


def test_canonical_url_is_none_when_absent():
    """Some pages ship no OpenGraph at all, hence a nullable source_url."""
    assert canonical_url(load("no_structured_data.html")) is None


# --- layering --------------------------------------------------------------


def test_stronger_layer_wins_when_layers_disagree():
    """The whole point of tiers: JSON-LD outranks og:site_name for brand."""
    soup = parse(
        '<html><head>'
        '<meta property="og:site_name" content="Example Store">'
        '<meta property="og:title" content="Widget | Example Store">'
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Widget","brand":{"@type":"Brand","name":"Real Maker"}}'
        "</script></head></html>"
    )
    bundle = CandidateBundle()
    for layer in (jsonld, microdata, meta):
        layer.extract(soup, bundle)

    assert bundle.best(BRAND).value == "Real Maker"
    assert bundle.best(NAME).value == "Widget"
    # The weaker readings survive as candidates rather than being discarded.
    assert "Example Store" in values(bundle, BRAND)


def test_running_a_layer_does_not_mutate_the_shared_tree():
    """dom.parse() output is shared by every layer, so none may modify it."""
    soup = load("jsonld_product.html")
    before = len(soup.find_all("script"))

    first = CandidateBundle()
    for layer in (jsonld, microdata, meta):
        layer.extract(soup, first)

    assert len(soup.find_all("script")) == before

    second = CandidateBundle()
    jsonld.extract(soup, second)
    assert second.best(NAME).value == "Structured Widget"


@pytest.mark.parametrize(
    "fixture",
    [
        "jsonld_product.html",
        "jsonld_graph.html",
        "jsonld_productgroup.html",
        "jsonld_malformed.html",
        "microdata_product.html",
        "microdata_bare_price.html",
        "meta_only.html",
        "no_structured_data.html",
    ],
)
def test_all_layers_run_clean_on_every_fixture(fixture):
    """No layer may raise on any input; one bad page must not kill a batch."""
    bundle = bundle_from(fixture)
    assert isinstance(bundle.to_prompt_dict(), dict)
