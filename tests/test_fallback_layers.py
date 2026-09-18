"""The tier-C and tier-D layers, plus the semantic serialiser.

Fixtures are hand-written from the specs and conventions each layer targets.
Nothing is copied from data/.
"""

from pathlib import Path

import pytest

from extraction.layers import embedded, jsonld, pickers, text
from extraction.candidates import (
    BRAND,
    COLORS,
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
from extraction.derive import derive_options
from extraction.dom import parse, semantic_html
from extraction.pipeline import build_bundle

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return parse((FIXTURES / name).read_text())


def run(layer, name: str) -> CandidateBundle:
    bundle = CandidateBundle()
    layer.extract(load(name), bundle)
    return bundle


def values(bundle: CandidateBundle, field: str) -> list:
    return [candidate.value for candidate in bundle.for_field(field)]


def axes(bundle: CandidateBundle) -> dict[str, list[str]]:
    variants = [candidate.value for candidate in bundle.all_at_best_tier(VARIANTS)]
    return {option.name: option.values for option in derive_options(variants)}


# --- embedded JSON (tier C) ------------------------------------------------


def test_embedded_finds_a_deeply_nested_product():
    bundle = run(embedded, "embedded_json.html")

    assert bundle.best(NAME).value == "Embedded Widget"
    assert bundle.best(PRICE).value == 88.50
    assert bundle.best(COMPARE_AT_PRICE).value == 110.00
    assert bundle.best(CURRENCY).value == "EUR"
    assert bundle.best(BRAND).value == "State Brand"
    assert bundle.best(COLORS).value == ["Ink"]
    assert bundle.best(IMAGE_URLS).value == ["https://cdn.test/embedded-1.jpg"]
    assert bundle.best(DESCRIPTION).value == "Found inside application state."


def test_embedded_is_tier_c_so_standards_outrank_it():
    bundle = run(embedded, "embedded_json.html")
    assert bundle.best(NAME).tier is Tier.C


def test_embedded_prefers_the_most_completely_described_object():
    """A recommendations entry has the same shape but fewer fields."""
    bundle = run(embedded, "embedded_json.html")
    assert "Cheaper Decoy" not in values(bundle, NAME)
    assert 4.00 not in values(bundle, PRICE)


def test_embedded_ignores_dicts_that_are_not_product_shaped():
    """A name alone is not enough; a nav entry has one."""
    bundle = run(embedded, "embedded_json.html")
    assert "Shop all" not in values(bundle, NAME)


def test_embedded_reads_json_from_an_attribute():
    """Some pages park the product record in a data-* attribute, not a script."""
    soup = parse(
        '<div data-preload=\'{"name":"Attribute Only","sku":"A-1","price":"9.99",'
        '"currencyCode":"USD","description":"From an attribute."}\'></div>'
    )
    bundle = CandidateBundle()
    embedded.extract(soup, bundle)
    assert bundle.best(NAME).value == "Attribute Only"
    assert bundle.best(PRICE).value == 9.99
    assert "attribute" in bundle.best(NAME).source


def test_embedded_reads_an_inline_script_assignment():
    soup = parse(
        "<script>window.__STATE__ = "
        '{"item":{"title":"Assigned Widget","sku":"S-1","price":3.5,"brand":"B"}};'
        "</script>"
    )
    bundle = CandidateBundle()
    embedded.extract(soup, bundle)
    assert bundle.best(NAME).value == "Assigned Widget"


def test_embedded_handles_nested_money_objects():
    soup = parse(
        '<script type="application/json">'
        '{"product":{"name":"Money Widget","sku":"M-1",'
        '"price":{"amount":42.5,"currencyCode":"CAD"}}}'
        "</script>"
    )
    bundle = CandidateBundle()
    embedded.extract(soup, bundle)
    assert bundle.best(PRICE).value == 42.5


def test_embedded_ignores_ld_json_which_a_stronger_layer_owns():
    bundle = run(embedded, "jsonld_product.html")
    assert len(bundle) == 0


def test_embedded_skips_a_mirrored_list_price():
    """Payloads often copy the current price into the list price when not on sale."""
    soup = parse(
        '<script type="application/json">'
        '{"p":{"name":"Flat","sku":"F","price":10.0,"listPrice":10.0}}'
        "</script>"
    )
    bundle = CandidateBundle()
    embedded.extract(soup, bundle)
    assert bundle.best(PRICE).value == 10.0
    assert bundle.best(COMPARE_AT_PRICE) is None


def test_embedded_walk_is_bounded_by_depth():
    """A product buried deeper than the depth guard is not found, by design."""
    payload = {"name": "Deep", "sku": "D"}
    for _ in range(embedded._MAX_DEPTH + 4):
        payload = {"nest": payload}
    assert list(embedded._product_shaped(payload)) == []

    shallow = {"name": "Shallow", "sku": "S"}
    for _ in range(4):
        shallow = {"nest": shallow}
    assert len(list(embedded._product_shaped(shallow))) == 1


def test_embedded_walk_is_bounded_by_node_budget():
    """A wide payload cannot make the search unbounded."""
    payload = {"items": [{"filler": index} for index in range(50_000)]}
    assert list(embedded._product_shaped(payload)) == []


def test_embedded_survives_unparseable_scripts():
    soup = parse('<script type="application/json">{not json</script>')
    bundle = CandidateBundle()
    embedded.extract(soup, bundle)
    assert len(bundle) == 0


def test_embedded_key_matching_ignores_case_and_underscores():
    for key in ("productName", "product_name", "PRODUCTNAME"):
        soup = parse(
            f'<script type="application/json">{{"p":{{"{key}":"Cased","sku":"C"}}}}</script>'
        )
        bundle = CandidateBundle()
        embedded.extract(soup, bundle)
        assert bundle.best(NAME).value == "Cased", key


# --- rendered text (tier D) ------------------------------------------------


def test_text_reads_the_sale_pair_in_order():
    """"£34.00  £42.50" -- the lower figure is what you pay."""
    bundle = run(text, "text_content.html")
    assert bundle.best(PRICE).value == 34.00
    assert bundle.best(COMPARE_AT_PRICE).value == 42.50


def test_text_handles_a_symbol_split_across_elements():
    """The symbol and the digits are separate spans, so text joining separates them."""
    bundle = run(text, "text_content.html")
    assert 34.00 in values(bundle, PRICE)


def test_text_currency_from_symbol_is_not_marked_inferred():
    bundle = run(text, "text_content.html")
    currency = bundle.best(CURRENCY)
    assert currency.value == "GBP"
    assert "inferred" not in currency.source


def test_ambiguous_symbol_resolved_by_language_region():
    soup = parse('<html lang="en-CA"><body><p>$19.99</p></body></html>')
    bundle = CandidateBundle()
    text.extract(soup, bundle)
    assert bundle.best(CURRENCY).value == "CAD"
    assert "inferred" not in bundle.best(CURRENCY).source


def test_ambiguous_symbol_without_region_says_inferred():
    """The word is a contract: the escalation policy greps the source for it."""
    soup = parse("<html><body><p>$19.99</p></body></html>")
    bundle = CandidateBundle()
    text.extract(soup, bundle)
    assert bundle.best(CURRENCY).value == "USD"
    assert "inferred" in bundle.best(CURRENCY).source


def test_text_price_candidates_record_how_often_a_value_appears():
    """Repetition is the only honest signal this layer has about which is real."""
    soup = parse("<html><body><p>$25.00</p><p>$25.00</p><p>$4.00 shipping</p></body></html>")
    bundle = CandidateBundle()
    text.extract(soup, bundle)
    top = bundle.best(PRICE)
    assert top.value == 25.00
    assert "seen 2x" in top.source


def test_distant_prices_are_not_treated_as_a_sale():
    """A shipping threshold in the footer is not the was-price."""
    bundle = run(text, "text_content.html")
    assert 150.00 not in values(bundle, COMPARE_AT_PRICE)


def test_implausible_ratios_are_not_treated_as_a_sale():
    """"£40.00, save £5.00" -- the second figure is a discount, not a former price."""
    soup = parse("<html><body><p>&pound;40.00 save &pound;5.00</p></body></html>")
    bundle = CandidateBundle()
    text.extract(soup, bundle)
    assert bundle.best(COMPARE_AT_PRICE) is None


def test_text_takes_the_first_h1_as_a_name():
    bundle = run(text, "text_content.html")
    assert bundle.best(NAME).value == "Rendered Widget"


def test_text_reads_a_feature_list_and_skips_chrome():
    bundle = run(text, "text_content.html")
    features = bundle.best(KEY_FEATURES).value
    assert "Forged steel body" in features
    # Breadcrumbs and footer links are lists too.
    assert "Home" not in features
    assert "Returns" not in features


def test_text_does_not_mutate_the_shared_tree():
    """Removing scripts here would delete another layer's only price source."""
    soup = load("jsonld_product.html")
    bundle = CandidateBundle()
    text.extract(soup, bundle)

    after = CandidateBundle()
    jsonld.extract(soup, after)
    assert after.best(PRICE).value == 49.95


def test_price_in_a_script_is_not_visible_text():
    soup = parse(
        '<html><body><script type="application/ld+json">'
        '{"@type":"Product","name":"Hidden","offers":{"@type":"Offer","price":"7.77","priceCurrency":"USD"}}'
        "</script><p>No price here.</p></body></html>"
    )
    bundle = CandidateBundle()
    text.extract(soup, bundle)
    assert bundle.for_field(PRICE) == []


# --- rendered variant pickers ---------------------------------------------


def test_axis_inferred_from_shared_prefix_of_accessible_names():
    """"Size Option: Small" x3 -- the shared leading text is the axis."""
    result = axes(run(pickers, "text_pickers.html"))
    assert result["Size"] == ["Small", "Medium", "Large"]


def test_axis_from_swatch_alt_text_strips_the_trailing_price():
    result = axes(run(pickers, "text_pickers.html"))
    assert result["Colour"] == ["Black", "Sand"]


def test_aria_pressed_toggle_group_is_a_picker():
    """A labelled group of toggle buttons, the ARIA pattern for a non-radio picker."""
    result = axes(run(pickers, "text_pickers.html"))
    assert result["Waist"] == ["30", "32", "34"]


def test_fieldset_legend_names_the_axis():
    result = axes(run(pickers, "text_pickers.html"))
    assert result["Fit"] == ["Regular", "Tall"]


def test_quantity_select_is_not_an_axis():
    result = axes(run(pickers, "text_pickers.html"))
    assert not any("quantity" in name.casefold() for name in result)
    assert "How many" not in result  # caught by the 1..n shape, not the label


def test_survey_question_is_not_an_axis():
    result = axes(run(pickers, "text_pickers.html"))
    assert not any(name.rstrip().endswith("?") for name in result)
    assert not any("owner" in name.casefold() for name in result)


def test_picker_inside_a_dialog_is_ignored():
    """A store locator is not the product's variant picker."""
    result = axes(run(pickers, "text_pickers.html"))
    assert "State" not in result
    assert not any("Alabama" in values for values in result.values())


def test_rendered_variants_are_single_axis_and_say_so():
    """A picker reveals axes, not combinations, and the source records that."""
    bundle = run(pickers, "text_pickers.html")
    for candidate in bundle.for_field(VARIANTS):
        assert len(candidate.value.option_values) == 1
        assert candidate.tier is Tier.D
        assert "combination unknown" in candidate.source


def test_declared_variants_outrank_rendered_ones():
    """A page with both must use the declared matrix."""
    soup = parse(
        '<html><body>'
        '<script type="application/ld+json">'
        '{"@type":"ProductGroup","name":"Both","variesBy":["https://schema.org/size"],'
        '"hasVariant":[{"@type":"Product","size":"S"},{"@type":"Product","size":"M"}]}'
        "</script>"
        '<div role="radiogroup">'
        '<button role="radio" aria-label="Size Option: WRONG" title="WRONG">WRONG</button>'
        '<button role="radio" aria-label="Size Option: ALSOWRONG" title="ALSOWRONG">ALSOWRONG</button>'
        "</div></body></html>"
    )
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    text.extract(soup, bundle)

    assert bundle.best(VARIANTS).tier is Tier.A
    assert axes(bundle) == {"Size": ["S", "M"]}


# --- semantic_html --------------------------------------------------------


def test_semantic_html_drops_presentation_keeps_meaning():
    soup = load("semantic_noise.html")
    out = semantic_html(soup)

    for noise in ('class="', 'style="', "onload=", "<style", "<template", "<svg", "tracking"):
        assert noise not in out, noise

    assert "Noisy Widget" in out
    assert "A widget with a lot of noise around it." in out
    assert 'itemprop="description"' in out
    assert 'alt="Noisy Widget front"' in out
    assert 'property="og:title"' in out  # og tags stay identifiable


def test_semantic_html_preserves_jsonld_verbatim():
    """Escalation re-reads this, so the strongest source must survive intact."""
    out = semantic_html(load("semantic_noise.html"))
    assert '"@type":"Product"' in out.replace(" ", "")
    assert '"priceCurrency":"USD"' in out.replace(" ", "")


def test_semantic_html_drops_application_state_scripts():
    """embedded.py already mined them; they are the largest remaining cost."""
    out = semantic_html(load("semantic_noise.html"))
    assert "hydration" not in out


def test_semantic_html_elides_inline_data_uris():
    """A dozen base64 images can otherwise consume the whole budget."""
    out = semantic_html(load("semantic_noise.html"))
    assert "base64" not in out
    assert "data:[elided]" in out
    assert 'alt="inline pixel"' in out  # the element itself is still evidence


def test_semantic_html_drops_responsive_source_lists():
    out = semantic_html(load("semantic_noise.html"))
    assert "w-320.jpg" not in out
    assert "w-1280.jpg" in out  # the <img> still carries the image


def test_semantic_html_keeps_canonical_and_drops_other_links():
    out = semantic_html(load("semantic_noise.html"))
    assert "https://shop.test/noisy" in out
    assert "app.css" not in out


def test_semantic_html_unwraps_bare_wrappers():
    soup = parse("<div><div><div><p>Only child</p></div></div></div>")
    out = semantic_html(soup)
    assert out.count("<div>") == 0
    assert "Only child" in out


def test_semantic_html_keeps_wrappers_that_carry_meaning():
    soup = parse('<div itemprop="brand"><span>Kept</span></div>')
    out = semantic_html(soup)
    assert 'itemprop="brand"' in out


def test_semantic_html_drops_empty_elements():
    out = semantic_html(load("semantic_noise.html"))
    assert "<span></span>" not in out


def test_semantic_html_respects_max_chars():
    out = semantic_html(load("semantic_noise.html"), max_chars=120)
    assert len(out) == 120


def test_semantic_html_does_not_mutate_the_shared_tree():
    """It works on a copy; the layers still need the original intact."""
    soup = load("semantic_noise.html")
    before_scripts = len(soup.find_all("script"))
    before_styles = len(soup.find_all("style"))

    semantic_html(soup)

    assert len(soup.find_all("script")) == before_scripts
    assert len(soup.find_all("style")) == before_styles

    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    assert bundle.best(PRICE).value == 5.00


def test_semantic_html_reduces_substantially():
    soup = load("semantic_noise.html")
    assert len(semantic_html(soup)) < 0.6 * len(str(soup))


# --- pipeline -------------------------------------------------------------


def test_pipeline_runs_every_layer_and_returns_the_tree():
    html = (FIXTURES / "jsonld_product.html").read_text()
    bundle, soup = build_bundle(html)

    assert bundle.best(NAME).value == "Structured Widget"
    assert soup.find("h1") is not None


def test_pipeline_layers_are_ordered_strongest_first():
    from extraction.layers import meta, microdata

    from extraction.pipeline import LAYERS

    assert LAYERS == (jsonld, microdata, meta, embedded, text, pickers)


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
        "embedded_json.html",
        "text_content.html",
        "text_pickers.html",
        "semantic_noise.html",
    ],
)
def test_pipeline_never_raises_and_always_serialises(fixture):
    html = (FIXTURES / fixture).read_text()
    bundle, soup = build_bundle(html)
    assert isinstance(bundle.to_prompt_dict(), dict)
    assert isinstance(semantic_html(soup, max_chars=4000), str)
