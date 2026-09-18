"""Grounding, provenance scoring, the trigger policy, and the second pass."""

from pathlib import Path

import pytest

from extraction import escalation, grounding, images
from extraction.dom import canonical_url
from extraction.candidates import (
    BRAND,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    KEY_FEATURES,
    NAME,
    PRICE,
    VARIANTS,
    CandidateBundle,
    Tier,
)
from extraction.escalation import merge, needs_escalation, score_tiers
from extraction.llm import FakeLLMClient
from extraction.pipeline import build_bundle, extract
from models import (
    Category,
    FactsResponse,
    Price,
    Product,
    ProseResponse,
    Variant,
    VariantAttribute,
)

FIXTURES = Path(__file__).parent / "fixtures"


def product(**overrides) -> Product:
    defaults = dict(
        name="Structured Widget",
        price=Price(price=49.95, currency="USD"),
        description="A widget.",
        key_features=["Durable"],
        image_urls=["https://cdn.test/widget-1.jpg"],
        category=Category(name="Home & Garden"),
        brand="Fixture Brand",
        colors=["Slate"],
        variants=[],
    )
    return Product(**{**defaults, **overrides})


def facts(**overrides) -> FactsResponse:
    defaults = dict(
        name="Structured Widget",
        brand="Fixture Brand",
        price=49.95,
        currency="USD",
        compare_at_price=None,
        colors=["Slate"],
        axes=[],
        video_url=None,
    )
    return FactsResponse(**{**defaults, **overrides})


def prose(**overrides) -> ProseResponse:
    defaults = dict(description="A widget for testing.", key_features=["Durable"])
    return ProseResponse(**{**defaults, **overrides})


def html(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- grounding: URLs -------------------------------------------------------


def test_a_fabricated_url_is_rejected():
    source = '<img src="https://cdn.test/products/real-photo-12345.jpg">'
    warnings = grounding.verify_urls(["https://cdn.test/products/invented-99999.jpg"], source)
    assert len(warnings) == 1
    assert "not found in source" in warnings[0]


def test_a_legitimately_normalised_url_is_kept():
    """Normalisation rewrote the URL, so whole-string matching would reject it.

    Matching on the longest path segment is what lets a stripped rendition
    parameter and a removed transformation segment still verify.
    """
    source = (
        '<img src="//cdn.test/is/image/wim/224626_1176_41?hei=1095&wid=950">'
        '<img src="https://cdn.test/a/images/t_default/w_640,c_limit/asset-abc123/photo.png">'
    )
    normalised = [
        "https://cdn.test/is/image/wim/224626_1176_41",
        "https://cdn.test/a/images/asset-abc123/photo.png",
    ]
    assert grounding.verify_urls(normalised, source) == []


def test_percent_encoded_paths_verify_against_a_decoded_source():
    source = '<img src="https://cdn.test/a/images/NIKE AIR FORCE 1.png">'
    assert grounding.verify_urls(["https://cdn.test/a/images/NIKE%20AIR%20FORCE%201.png"], source) == []


def test_a_url_with_no_distinctive_segment_is_flagged():
    assert grounding.verify_urls(["https://cdn.test/a/b"], "<html></html>")


def test_every_harvested_url_on_every_fixture_verifies():
    """A regression guard on normalisation itself, which is what this really tests."""
    for fixture in ("images_gallery.html", "jsonld_product.html", "microdata_product.html"):
        source = html(fixture)
        bundle, soup = build_bundle(source)
        urls = images.harvest(soup, bundle, canonical_url(soup))
        assert grounding.verify_urls(urls, source) == [], fixture


# --- grounding: what is deliberately not checked -------------------------


def test_prices_are_not_verified_by_substring():
    """Removed on purpose: the check could not fail, so it carried no signal.

    A price's bare numeric string occurs 175 times in one sample page's source,
    61 in another and 29 in a third. Provenance tier and the compare-at candidate
    requirement are the real constraints; see grounding.py.
    """
    assert not hasattr(grounding, "verify_price")


def test_brand_grounding_ignores_case_and_short_values():
    assert grounding.verify_text("DeWalt", "<p>dewalt tools</p>", label="brand") == []
    assert grounding.verify_text("", "<p>x</p>", label="brand") == []
    assert grounding.verify_text("Invented Co", "<p>nothing</p>", label="brand")


# --- provenance scoring ----------------------------------------------------


def test_declared_values_score_at_their_source_tier():
    bundle, _ = build_bundle(html("jsonld_product.html"))
    tiers = score_tiers(bundle, product())

    assert tiers[NAME] == "A"
    assert tiers[PRICE] == "A"
    assert tiers[CURRENCY] == "A"
    assert tiers[BRAND] == "A"


def test_a_value_no_candidate_offered_scores_tier_e():
    """This is the fabrication check: an invented value has no provenance."""
    bundle, _ = build_bundle(html("jsonld_product.html"))
    tiers = score_tiers(bundle, product(brand="Invented Brand", name="Invented Name"))

    assert tiers[BRAND] == "E"
    assert tiers[NAME] == "E"


def test_a_fabricated_compare_at_scores_tier_e():
    """The failure that had to be caught by eye during arbitration, now visible.

    A delivery threshold read as a former price has no compare_at_price candidate
    behind it, so it scores E on a field that should never be E.
    """
    bundle, _ = build_bundle(html("jsonld_product.html"))
    tiers = score_tiers(
        bundle, product(price=Price(price=170.0, currency="USD", compare_at_price=200.0))
    )
    assert tiers[COMPARE_AT_PRICE] == "E"


def test_a_supported_compare_at_scores_at_its_tier():
    bundle, _ = build_bundle(html("jsonld_graph.html"))
    tiers = score_tiers(
        bundle, product(price=Price(price=80.0, currency="GBP", compare_at_price=120.0))
    )
    assert tiers[COMPARE_AT_PRICE] == "A"


def test_a_trimmed_name_still_credits_its_source():
    """Arbitration's job is to strip shop furniture off a title.

    The trimmed result should be credited to what it was trimmed from, not counted
    as invention.
    """
    bundle, _ = build_bundle(html("meta_only.html"))
    tiers = score_tiers(bundle, product(name="Meta Widget"))
    assert tiers[NAME] == "B"


def test_synthesised_prose_scores_tier_e():
    """Description and features have no standard behind them; E is correct."""
    bundle, _ = build_bundle(html("no_structured_data.html"))
    tiers = score_tiers(
        bundle, product(description="Freshly written copy.", key_features=["Invented"])
    )
    assert tiers[DESCRIPTION] == "E"
    assert tiers[KEY_FEATURES] == "E"


def test_the_strongest_matching_candidate_wins():
    bundle = CandidateBundle()
    bundle.add(BRAND, "Real Maker", "meta og:site_name", Tier.D)
    bundle.add(BRAND, "Real Maker", "JSON-LD brand", Tier.A)
    assert score_tiers(bundle, product(brand="Real Maker"))[BRAND] == "A"


def test_images_score_through_normalisation():
    """The harvested URL was rewritten, so a raw-string comparison would say E."""
    bundle = CandidateBundle()
    bundle.add(IMAGE_URLS, ["https://cdn.test/p/a.jpg?w=200&q=80"], "JSON-LD image", Tier.A)
    tiers = score_tiers(bundle, product(image_urls=["https://cdn.test/p/a.jpg"]))
    assert tiers[IMAGE_URLS] == "A"


def test_variants_score_by_their_option_values():
    bundle, _ = build_bundle(html("jsonld_productgroup.html"))
    variants = [candidate.value for candidate in bundle.all_at_best_tier(VARIANTS)]
    assert score_tiers(bundle, product(variants=variants))[VARIANTS] == "A"


def test_absent_fields_are_not_scored():
    bundle, _ = build_bundle(html("jsonld_product.html"))
    tiers = score_tiers(bundle, product(colors=[], variants=[]))
    assert "colors" not in tiers
    assert VARIANTS not in tiers


# --- trigger policy --------------------------------------------------------


def assess(fixture: str, item: Product, warnings=None, repairs=0):
    bundle, _ = build_bundle(html(fixture))
    tiers = score_tiers(bundle, item)
    return needs_escalation(bundle, item, tiers, warnings or [], repairs=repairs)


def test_a_well_sourced_page_does_not_flag():
    reasons = assess("jsonld_product.html", product())
    assert reasons == []


def test_a_text_only_price_flags():
    """The same amount from JSON-LD does not, which is the whole point of tiers."""
    source = "<html lang='en-US'><body><h1>Widget</h1><p>$41.00</p></body></html>"
    bundle, _ = build_bundle(source)
    item = product(price=Price(price=41.0, currency="USD"))
    tiers = score_tiers(bundle, item)

    assert tiers[PRICE] == "D"
    assert any("tier D" in reason for reason in needs_escalation(bundle, item, tiers, []))


def test_a_declared_price_does_not_flag():
    bundle, _ = build_bundle(html("jsonld_product.html"))
    item = product()
    tiers = score_tiers(bundle, item)
    assert not any("price" in reason for reason in needs_escalation(bundle, item, tiers, []))


def test_an_inferred_currency_flags():
    """text.py writes the word "inferred" specifically so this can read it."""
    source = "<html><body><h1>Widget</h1><p>$41.00</p></body></html>"  # no lang region
    bundle, _ = build_bundle(source)
    item = product(price=Price(price=41.0, currency="USD"))
    reasons = needs_escalation(bundle, item, score_tiers(bundle, item), [])
    assert any("inferred" in reason for reason in reasons)


def test_a_brand_from_the_site_name_flags():
    bundle, _ = build_bundle(html("meta_only.html"))
    item = product(name="Meta Widget", brand="Example Store")
    reasons = needs_escalation(bundle, item, score_tiers(bundle, item), [])
    assert any("site name" in reason for reason in reasons)


def test_a_declared_brand_does_not_flag():
    bundle, _ = build_bundle(html("meta_only.html"))
    item = product(name="Meta Widget", brand="Meta Maker")
    reasons = needs_escalation(bundle, item, score_tiers(bundle, item), [])
    assert not any("brand" in reason for reason in reasons)


def test_no_images_flags():
    reasons = assess("jsonld_product.html", product(image_urls=[]))
    assert any("no images" in reason for reason in reasons)


def test_a_small_but_real_gallery_does_not_flag():
    """Three images is a real gallery; a threshold above one is a false positive."""
    reasons = assess(
        "jsonld_product.html",
        product(image_urls=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg", "https://cdn.test/c.jpg"]),
    )
    assert not any("image" in reason for reason in reasons)


def test_empty_variants_alone_never_flag():
    """A drill and a floor lamp legitimately have none."""
    reasons = assess("jsonld_product.html", product(variants=[]))
    assert not any("variant" in reason for reason in reasons)


def test_a_grounding_failure_flags():
    reasons = assess(
        "jsonld_product.html", product(), warnings=["image URL not found in source: x"]
    )
    assert any("grounding failure" in reason for reason in reasons)


def test_repairs_flag():
    reasons = assess("jsonld_product.html", product(), repairs=1)
    assert any("repair" in reason for reason in reasons)


# --- merge -----------------------------------------------------------------


def test_merge_replaces_only_weak_fields():
    """A declared price must survive a full-page re-read; that would be a downgrade."""
    item = product()
    tiers = {NAME: "A", PRICE: "A", CURRENCY: "A", BRAND: "D"}
    merged, changes = merge(
        item, facts(name="Rewritten", price=9.99, currency="EUR", brand="Real Maker"), tiers
    )

    assert merged.name == "Structured Widget"
    assert merged.price.price == 49.95
    assert merged.price.currency == "USD"
    assert merged.brand == "Real Maker"
    assert len(changes) == 1


def test_merge_reports_no_changes_when_the_second_pass_agrees():
    item = product()
    tiers = {BRAND: "D"}
    merged, changes = merge(item, facts(), tiers)
    assert changes == []
    assert merged.brand == item.brand


def test_merge_rejects_an_implausible_currency():
    item = product()
    merged, _ = merge(item, facts(currency="dollars"), {CURRENCY: "D"})
    assert merged.price.currency == "USD"


def test_merge_rejects_a_compare_at_below_price():
    item = product()
    merged, _ = merge(item, facts(compare_at_price=10.0), {COMPARE_AT_PRICE: "E"})
    assert merged.price.compare_at_price is None


def test_merge_accepts_a_higher_compare_at():
    item = product()
    merged, changes = merge(item, facts(compare_at_price=80.0), {COMPARE_AT_PRICE: "E"})
    assert merged.price.compare_at_price == 80.0
    assert any("compare_at_price" in change for change in changes)


def test_merge_never_touches_images_or_category():
    item = product()
    merged, _ = merge(item, facts(), {field: "E" for field in (NAME, BRAND, PRICE)})
    assert merged.image_urls == item.image_urls
    assert merged.category == item.category


# --- wiring ---------------------------------------------------------------


async def test_metadata_is_populated_without_escalation():
    client = FakeLLMClient([facts(), prose()])
    envelope = await extract(
        html("jsonld_product.html"), "f.html", client, category_resolver=None
    )

    assert envelope.extraction.field_tiers[PRICE] == "A"
    assert envelope.extraction.escalated is False
    assert len(client.calls) == 2


async def test_the_flag_off_means_no_second_call_even_when_flagged():
    """Scoring and grounding are free and always run; the second pass is not."""
    source = "<html><body><h1>Widget</h1><p>$41.00</p></body></html>"
    client = FakeLLMClient([facts(price=41.0), prose()])
    envelope = await extract(source, "f.html", client, category_resolver=None)

    assert any("weak extraction" in w for w in envelope.extraction.warnings)
    assert envelope.extraction.escalated is False
    assert len(client.calls) == 2


async def test_the_flag_on_fires_exactly_one_extra_call():
    source = "<html><body><h1>Widget</h1><p>$41.00</p></body></html>"
    client = FakeLLMClient([facts(price=41.0), prose(), facts(price=41.0, brand="Better")])
    envelope = await extract(
        source, "f.html", client, category_resolver=None, escalate=True
    )

    assert envelope.extraction.escalated is True
    assert len(client.calls) == 3
    assert client.calls[2]["model"] == "openai/gpt-5-mini"


async def test_escalation_does_not_fire_on_a_clean_page():
    client = FakeLLMClient([facts(), prose()])
    envelope = await extract(
        html("jsonld_product.html"), "f.html", client, category_resolver=None, escalate=True
    )
    assert envelope.extraction.escalated is False
    assert len(client.calls) == 2


async def test_escalation_is_one_attempt_not_a_loop():
    source = "<html><body><h1>Widget</h1><p>$41.00</p></body></html>"
    client = FakeLLMClient([facts(price=41.0), prose(), None])
    envelope = await extract(
        source, "f.html", client, category_resolver=None, escalate=True
    )
    assert len(client.calls) == 3
    assert envelope.extraction.escalated is False  # nothing came back to merge


async def test_declared_fields_survive_escalation_while_the_weak_one_is_replaced():
    """A page flagged on brand alone must keep everything it sourced properly.

    meta_only.html declares name, price and currency through OpenGraph (tier B) but
    only offers the shop's own name as a brand (tier D), so it flags on brand and
    nothing else. A second pass that tries to rewrite the rest must be ignored.
    """
    first = facts(name="Meta Widget", brand="Example Store", price=59.0, currency="AUD")
    hijack = facts(name="Hijacked", brand="Meta Maker", price=1.0, currency="EUR")

    client = FakeLLMClient([first, prose(), hijack])
    envelope = await extract(
        html("meta_only.html"), "f.html", client, category_resolver=None, escalate=True
    )

    assert envelope.extraction.escalated is True
    assert envelope.product.name == "Meta Widget"
    assert envelope.product.price.price == 59.0
    assert envelope.product.price.currency == "AUD"
    assert envelope.product.brand == "Meta Maker"  # the weak field, corrected
    assert any("brand" in change for change in envelope.extraction.warnings)
