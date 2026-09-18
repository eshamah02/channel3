"""LLM arbitration, entirely offline against FakeLLMClient.

Nothing here spends a token. The point of putting the model behind a Protocol is
that the repair logic, the assembly invariants and the prompt content are all
testable before any live call happens.
"""

from pathlib import Path

import pytest

import ai
from config import FACTS_MODEL, PROSE_MODEL
from extraction import images, llm, pipeline
from extraction.candidates import VARIANTS
from extraction.llm import FakeLLMClient, UsageRecord, cost_of
from extraction.pipeline import ExtractionError, build_bundle, extract
from models import (
    Category,
    ExtractedProduct,
    FactsResponse,
    ProseResponse,
    VariantAxis,
)

FIXTURES = Path(__file__).parent / "fixtures"


def prompt_text(client) -> str:
    """Every message body the client was sent, for asserting on prompt content."""
    return "\n".join(
        message["content"]
        for call in client.calls
        for message in call["input"]
        if isinstance(message.get("content"), str)
    )


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
    defaults = dict(
        description="A widget for testing.",
        key_features=["Durable", "Washable"],
    )
    return ProseResponse(**{**defaults, **overrides})


def html(name: str = "jsonld_product.html") -> str:
    return (FIXTURES / name).read_text()


async def run(fixture: str = "jsonld_product.html", *, responses=None, **kwargs):
    """Extract a fixture against a fake client.

    Category resolution is off unless a test asks for it: these tests are about
    arbitration and assembly, and the descent would otherwise need several queued
    responses in every one of them. tests/test_category.py covers the descent, and
    the tests below cover the seam between the two.
    """
    kwargs.setdefault("category_resolver", None)
    client = FakeLLMClient(responses if responses is not None else [facts(), prose()])
    result = await extract(html(fixture), f"tests/fixtures/{fixture}", client, **kwargs)
    return result, client


# --- happy path ------------------------------------------------------------


async def test_extract_produces_a_validated_envelope():
    envelope, client = await run()

    assert isinstance(envelope, ExtractedProduct)
    assert envelope.product.name == "Structured Widget"
    assert envelope.product.price.price == 49.95
    assert envelope.product.price.currency == "USD"
    assert envelope.product.description == "A widget for testing."
    assert envelope.slug == "structured-widget"
    assert envelope.source_url == "https://shop.test/products/gallery-widget" or envelope.source_url is None
    assert len(client.calls) == 2


async def test_two_calls_are_made_with_the_configured_models():
    _, client = await run()
    assert client.calls[0]["model"] == FACTS_MODEL
    assert client.calls[1]["model"] == PROSE_MODEL


async def test_reasoning_effort_is_pinned_low_on_both_calls():
    """Measured 7.3x cheaper, and it removes the run-to-run token variance."""
    _, client = await run()
    for call in client.calls:
        assert call["kwargs"]["reasoning"] == {"effort": "low"}


async def test_content_hash_and_id_are_populated():
    envelope, _ = await run()
    assert len(envelope.content_hash) == 64
    assert len(envelope.id) == 16


# --- images are not the model's business -----------------------------------


async def test_assembled_images_equal_harvest_output_exactly():
    """The model has no image_urls field, so it cannot truncate or rewrite them."""
    envelope, _ = await run("images_gallery.html")

    bundle, soup = build_bundle(html("images_gallery.html"))
    expected = images.harvest(soup, bundle, envelope.source_url)

    assert envelope.product.image_urls == expected
    assert expected  # the fixture does have images, so this is a real comparison


async def test_facts_response_has_no_image_field():
    assert "image_urls" not in FactsResponse.model_fields


async def test_missing_images_are_recorded_as_a_warning():
    envelope, _ = await run("no_structured_data.html")
    assert any("image" in warning for warning in envelope.extraction.warnings)


# --- variants: declared data bypasses the model ---------------------------


async def test_declared_variants_are_used_verbatim_and_not_sent_to_the_model():
    """Re-emitting a long declared matrix is transcription, which models do badly.

    It is also the single largest part of the prompt on a page with a full size
    run, so skipping it is both safer and cheaper.
    """
    envelope, client = await run("jsonld_productgroup.html")

    assert len(envelope.product.variants) == 3
    assert envelope.product.variants[0].sku == "TEE-CHAR-S"
    assert envelope.product.variants[0].price.price == 32.00

    facts_prompt = client.calls[0]["input"][-1]["content"]
    assert "TEE-CHAR-S" not in facts_prompt
    assert "Return an empty list" in client.calls[0]["input"][0]["content"]


async def test_declared_variants_shrink_the_prompt():
    with_variants, _ = build_bundle(html("jsonld_productgroup.html"))
    full = len(str(with_variants.to_prompt_dict()))

    messages = pipeline._facts_messages(with_variants, variants_declared=True)
    trimmed = len(messages[-1]["content"])

    assert trimmed < full


async def test_axes_are_requested_when_nothing_declares_the_matrix():
    _, client = await run("text_pickers.html")
    system = client.calls[0]["input"][0]["content"]
    assert "rendered picker" in system
    assert "Return an empty list" not in system


async def test_model_axes_become_single_axis_variants():
    """A picker gives axes, not combinations, so no combination is invented."""
    envelope, _ = await run(
        "text_pickers.html",
        responses=[
            facts(axes=[VariantAxis(name="Size", values=["S", "M", "L"])]),
            prose(),
        ],
    )

    assert len(envelope.product.variants) == 3
    for variant in envelope.product.variants:
        assert len(variant.option_values) == 1
        assert variant.option_values[0].name == "Size"
    assert [option.values for option in envelope.options] == [["S", "M", "L"]]


async def test_blank_axis_names_and_values_are_dropped():
    envelope, _ = await run(
        "text_pickers.html",
        responses=[
            facts(
                axes=[
                    VariantAxis(name="  ", values=["X"]),
                    VariantAxis(name="Size", values=["S", "  ", ""]),
                ]
            ),
            prose(),
        ],
    )
    assert len(envelope.product.variants) == 1
    assert envelope.product.variants[0].option_values[0].value == "S"


async def test_declared_variants_win_over_stray_model_axes():
    envelope, _ = await run(
        "jsonld_productgroup.html",
        responses=[facts(axes=[VariantAxis(name="Bogus", values=["X"])]), prose()],
    )
    assert len(envelope.product.variants) == 3
    assert any("ignored model-supplied axes" in w for w in envelope.extraction.warnings)


async def test_options_are_derived_not_generated():
    """Axes come from derive_options over the variants, including a sparse one.

    Sand/M is never offered, so Size still lists both values; and Fit appears from
    a single variant's additionalProperty, which is exactly the sparse case the
    union-per-axis rule exists for.
    """
    envelope, _ = await run("jsonld_productgroup.html")
    axes = {option.name: option.values for option in envelope.options}
    assert axes == {
        "Color": ["Charcoal", "Sand"],
        "Size": ["S", "M"],
        "Fit": ["Relaxed"],
    }


async def test_colors_never_contradict_variants():
    """derive_colors prefers the model's list and falls back to a colour axis."""
    envelope, _ = await run(
        "jsonld_productgroup.html", responses=[facts(colors=[]), prose()]
    )
    assert envelope.product.colors == ["Charcoal", "Sand"]


# --- price invariants ------------------------------------------------------


async def test_compare_at_equal_to_price_is_dropped():
    """The rendered-text sale heuristic can pair a figure with the real price.

    A compare-at equal to the price is a discount of nothing, so it is enforced
    here rather than trusted to the prompt.
    """
    envelope, _ = await run(responses=[facts(price=29.95, compare_at_price=29.95), prose()])
    assert envelope.product.price.compare_at_price is None


async def test_compare_at_below_price_is_dropped():
    envelope, _ = await run(responses=[facts(price=40.0, compare_at_price=30.0), prose()])
    assert envelope.product.price.compare_at_price is None


async def test_compare_at_without_a_candidate_is_dropped():
    """Observed on a real page: the model promoted a delivery threshold to a discount.

    The page said "free delivery over 200 USD" and the price was 170, so a larger
    number in the right currency was available and got read as a former price --
    inventing a saving. The bundle held no compare_at_price candidate at all.
    """
    envelope, _ = await run(
        "jsonld_product.html",  # an Offer with price only, no highPrice
        responses=[facts(price=170.0, compare_at_price=200.0), prose()],
    )
    assert envelope.product.price.compare_at_price is None
    assert any("no candidate for that field" in w for w in envelope.extraction.warnings)


async def test_genuine_compare_at_survives_when_a_candidate_supports_it():
    """The graph fixture's AggregateOffer carries highPrice 120 above lowPrice 80."""
    envelope, _ = await run(
        "jsonld_graph.html",
        responses=[facts(price=80.0, currency="GBP", compare_at_price=120.0), prose()],
    )
    assert envelope.product.price.compare_at_price == 120.0


async def test_compare_at_is_matched_to_two_decimal_places():
    envelope, _ = await run(
        "jsonld_graph.html",
        responses=[facts(price=80.0, currency="GBP", compare_at_price=120.004), prose()],
    )
    assert envelope.product.price.compare_at_price == 120.004


async def test_currency_is_upper_cased():
    envelope, _ = await run(responses=[facts(currency=" gbp "), prose()])
    assert envelope.product.price.currency == "GBP"


# --- repair loop -----------------------------------------------------------


async def test_an_invalid_response_triggers_exactly_one_repair():
    client = FakeLLMClient([facts(currency="dollars"), facts(currency="USD"), prose()])
    envelope = await extract(html(), "f.html", client, category_resolver=None)

    assert envelope.product.price.currency == "USD"
    assert len(client.calls) == 3  # facts, repair, prose
    assert "rejected" in client.calls[1]["input"][-1]["content"]


@pytest.mark.parametrize(
    "bad,complaint",
    [
        (dict(name="   "), "name"),
        (dict(brand=""), "brand"),
        (dict(price=0.0), "price"),
        (dict(price=-5.0), "price"),
        (dict(currency="US"), "currency"),
        (dict(currency="US$"), "currency"),
    ],
)
async def test_each_semantic_failure_is_rejected_and_explained(bad, complaint):
    client = FakeLLMClient([facts(**bad), facts(), prose()])
    await extract(html(), "f.html", client, category_resolver=None)
    assert complaint in client.calls[1]["input"][-1]["content"]


async def test_a_refusal_is_treated_as_a_failed_attempt():
    client = FakeLLMClient([None, facts(), prose()])
    envelope = await extract(html(), "f.html", client, category_resolver=None)
    assert envelope.product.name == "Structured Widget"


async def test_three_consecutive_failures_raise_rather_than_loop():
    client = FakeLLMClient([facts(price=0.0), facts(price=0.0), facts(price=0.0)])
    with pytest.raises(ExtractionError, match="after 3 attempts"):
        await extract(html(), "f.html", client, category_resolver=None)
    assert len(client.calls) == 3


async def test_prose_refusal_degrades_instead_of_failing():
    """Description and features are synthesis; a page is still servable without."""
    client = FakeLLMClient([facts(), None])
    envelope = await extract(html(), "f.html", client, category_resolver=None)
    assert envelope.product.description == ""
    assert envelope.product.key_features == []


# --- prompt hygiene -------------------------------------------------------


@pytest.mark.parametrize("fixture", ["jsonld_product.html", "text_pickers.html"])
async def test_prompts_name_no_site_and_carry_no_examples(fixture):
    """The brief disqualifies page-specific hints drawn from the provided data."""
    _, client = await run(fixture)
    text_sent = prompt_text(client).casefold()
    for banned in ("nike", "llbean", "l.l.bean", "adaysmarch", "acehardware", "dewalt"):
        assert banned not in text_sent, banned


async def test_facts_prompt_explains_the_tiers_generically():
    _, client = await run()
    system = client.calls[0]["input"][0]["content"]
    for tier in ("A", "B", "C", "D"):
        assert f"\n  {tier} " in system


async def test_prose_prompt_carries_the_resolved_name_and_an_excerpt():
    _, client = await run()
    body = client.calls[1]["input"][-1]["content"]
    assert "Product: Structured Widget" in body
    assert "Page excerpt:" in body


async def test_prose_excerpt_is_bounded():
    _, client = await run("jsonld_product.html")
    body = client.calls[1]["input"][-1]["content"]
    assert len(body) < pipeline._PROSE_EXCERPT_CHARS + 500


# --- category seam --------------------------------------------------------


async def test_placeholder_category_is_used_and_flagged():
    """Task 7 supplies the resolver; until then the gap must be visible."""
    envelope, _ = await run()
    assert envelope.product.category.name == pipeline._PLACEHOLDER_CATEGORY
    assert any("category not resolved" in w for w in envelope.extraction.warnings)


async def test_placeholder_is_a_real_taxonomy_entry():
    Category(name=pipeline._PLACEHOLDER_CATEGORY)


async def test_the_descent_is_wired_in_by_default():
    """No resolver argument means the real taxonomy descent runs."""
    from models import CategoryChoice

    client = FakeLLMClient(
        [facts(), prose(), CategoryChoice(choice=2), CategoryChoice(choice=1), CategoryChoice(choice=7)]
    )
    envelope = await extract(html(), "f.html", client)

    assert envelope.product.category.name == "Apparel & Accessories > Clothing > Pants"
    assert not any("category" in w for w in envelope.extraction.warnings)
    assert len(client.calls) == 5  # facts, prose, three descent steps


async def test_the_descent_sees_the_cleaned_name_and_description():
    """Not the raw candidates: it needs the trimmed name and written description.

    That is also why it runs after the prose call rather than straight after facts.
    """
    from models import CategoryChoice

    client = FakeLLMClient(
        [
            facts(name="Miller Trousers"),
            prose(description="Tailored cotton trousers."),
            *[CategoryChoice(choice=1)] * 8,
        ]
    )
    await extract(html(), "f.html", client)

    descent_prompt = client.calls[2]["input"][-1]["content"]
    assert "Product: Miller Trousers" in descent_prompt
    assert "Tailored cotton trousers." in descent_prompt


async def test_a_failed_descent_falls_back_without_killing_the_page():
    """One bad page must not abort a batch, and the compromise must be visible."""
    client = FakeLLMClient([facts(), prose(), None, None])
    envelope = await extract(html(), "f.html", client)

    assert envelope.product.category.name == pipeline._PLACEHOLDER_CATEGORY
    assert any("category resolution failed" in w for w in envelope.extraction.warnings)


async def test_a_category_resolver_is_used_when_supplied():
    seen = {}

    async def resolver(name, description, client):
        seen["name"] = name
        seen["description"] = description
        return Category(name="Apparel & Accessories")

    envelope, _ = await run(category_resolver=resolver)

    assert envelope.product.category.name == "Apparel & Accessories"
    assert seen["name"] == "Structured Widget"
    assert seen["description"] == "A widget for testing."
    assert not any("category not resolved" in w for w in envelope.extraction.warnings)


# --- cost accounting ------------------------------------------------------


class _FakeDetails:
    def __init__(self, reasoning_tokens: int) -> None:
        self.reasoning_tokens = reasoning_tokens


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens, reasoning_tokens) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.output_tokens_details = _FakeDetails(reasoning_tokens)


class _FakeResponse:
    def __init__(self, model, input_tokens, output_tokens, reasoning_tokens) -> None:
        self.model = model
        self.usage = _FakeUsage(input_tokens, output_tokens, reasoning_tokens)


def test_reasoning_tokens_are_priced_once():
    """The measured hello-world shape: 59 in, 1106 out, of which 1024 reasoning.

    Their _log_usage prints $0.000855 for this because it adds the reasoning
    tokens again at the output rate. Reasoning is a subset of output_tokens, so the
    correct figure is $0.000445.
    """
    model = "openai/gpt-5-nano"
    prices = ai.MODEL_PRICES[model]

    expected = (59 / 1e6) * prices["input"] + (1106 / 1e6) * prices["output"]
    assert cost_of(model, 59, 1106) == pytest.approx(expected)
    assert cost_of(model, 59, 1106) == pytest.approx(0.000445, abs=1e-6)

    double_counted = expected + (1024 / 1e6) * prices["output"]
    assert double_counted == pytest.approx(0.000855, abs=1e-6)


def test_unknown_model_costs_zero_rather_than_raising():
    """A new model id must not abort a batch of fifty million pages."""
    assert cost_of("vendor/not-in-price-table", 1000, 2000) == 0.0


def test_client_records_and_total_accumulate():
    llm.reset_usage_total()
    client = llm.OpenRouterClient()
    client._record(_FakeResponse("openai/gpt-5-nano", 59, 1106, 1024))

    assert len(client.records) == 1
    record = client.records[0]
    assert record.reasoning_tokens == 1024
    assert record.cost_usd == pytest.approx(0.000445, abs=1e-6)
    assert llm.USAGE_TOTAL["calls"] == 1
    assert llm.USAGE_TOTAL["cost_usd"] == pytest.approx(0.000445, abs=1e-6)
    llm.reset_usage_total()


def test_cost_since_isolates_one_products_calls():
    """Per-product cost is a slice of the client's own records, not a global delta."""
    llm.reset_usage_total()
    client = llm.OpenRouterClient()
    client._record(_FakeResponse("openai/gpt-5-nano", 1000, 1000, 0))
    mark = len(client.records)
    client._record(_FakeResponse("openai/gpt-5-nano", 2000, 2000, 0))
    client._record(_FakeResponse("openai/gpt-5-nano", 3000, 3000, 0))

    assert client.cost_since(mark) == pytest.approx(
        cost_of("openai/gpt-5-nano", 5000, 5000)
    )
    llm.reset_usage_total()


def test_response_without_usage_does_not_raise():
    client = llm.OpenRouterClient()
    client._record(object())
    assert client.records == []


async def test_extract_records_per_product_cost():
    client = FakeLLMClient([facts(), prose()])
    client.records.append(
        UsageRecord("prior/model", 999, 999, 0, 1.23)  # a previous product's call
    )
    envelope = await extract(html(), "f.html", client, category_resolver=None)
    # The fake reports zero cost per call, so the prior product's 1.23 must not
    # leak into this one.
    assert envelope.extraction.cost_usd == 0.0


def test_ai_module_is_not_monkeypatched():
    """ai.py is the provided helper and stays exactly as delivered."""
    assert not hasattr(ai, "usage_scope")
    assert not hasattr(ai, "USAGE_TOTAL")
