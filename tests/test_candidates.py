"""The candidate container and value coercion."""

import pytest

from extraction.candidates import (
    ALL_FIELDS,
    BRAND,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    NAME,
    PRICE,
    VARIANTS,
    Candidate,
    CandidateBundle,
    Tier,
    coerce_number,
    coerce_text_list,
)
from models import Variant, VariantAttribute


def test_unknown_field_is_rejected():
    """The vocabulary is fixed so two layers can't split one field in two."""
    with pytest.raises(ValueError, match="field vocabulary"):
        Candidate(field="prise", value=1.0, source="typo", tier=Tier.A)


def test_every_vocabulary_field_is_accepted():
    for field in ALL_FIELDS:
        Candidate(field=field, value="x", source="test", tier=Tier.A)


def test_add_skips_empty_values():
    """Layers hand over whatever a standard gave them; filtering lives here."""
    bundle = CandidateBundle()
    for empty in (None, "", "   ", [], ["", "  "], {}):
        assert bundle.add(NAME, empty, "test", Tier.A) is None
    assert len(bundle) == 0


def test_add_collapses_whitespace():
    """Pretty-printed markup leaves newlines and runs of spaces in text nodes."""
    bundle = CandidateBundle()
    bundle.add(NAME, "  Wool\n      Overshirt   Navy  ", "test", Tier.A)
    assert bundle.best(NAME).value == "Wool Overshirt Navy"


def test_add_keeps_zero_and_false():
    """0.0 is a legitimate price and False a legitimate stock state."""
    bundle = CandidateBundle()
    assert bundle.add(PRICE, 0.0, "test", Tier.A) is not None
    assert bundle.best(PRICE).value == 0.0


def test_best_prefers_stronger_tier_regardless_of_insertion_order():
    bundle = CandidateBundle()
    bundle.add(BRAND, "Example Store", "og:site_name", Tier.D)
    bundle.add(BRAND, "Real Maker", "JSON-LD brand", Tier.A)
    assert bundle.best(BRAND).value == "Real Maker"


def test_ties_broken_by_first_seen():
    bundle = CandidateBundle()
    bundle.add(NAME, "First", "a", Tier.A)
    bundle.add(NAME, "Second", "b", Tier.A)
    assert bundle.best(NAME).value == "First"


def test_best_returns_none_for_absent_field():
    assert CandidateBundle().best(PRICE) is None


def test_for_field_orders_by_tier():
    bundle = CandidateBundle()
    bundle.add(NAME, "d", "text", Tier.D)
    bundle.add(NAME, "b", "og:title", Tier.B)
    bundle.add(NAME, "a", "JSON-LD", Tier.A)
    assert [c.tier for c in bundle.for_field(NAME)] == [Tier.A, Tier.B, Tier.D]


def test_all_at_best_tier_collects_the_whole_group():
    """A 25-variant matrix arrives as 25 candidates; consumers want them all."""
    bundle = CandidateBundle()
    for size in ("S", "M", "L"):
        bundle.add(
            VARIANTS,
            Variant(option_values=[VariantAttribute(name="Size", value=size)]),
            "JSON-LD hasVariant",
            Tier.A,
        )
    bundle.add(VARIANTS, Variant(option_values=[VariantAttribute(name="Size", value="XL")]), "text", Tier.D)

    assert len(bundle.all_at_best_tier(VARIANTS)) == 3
    assert bundle.all_at_best_tier(DESCRIPTION) == []


def test_fields_reports_only_populated_fields_in_order():
    bundle = CandidateBundle()
    bundle.add(NAME, "n", "a", Tier.A)
    bundle.add(PRICE, 1.0, "a", Tier.A)
    bundle.add(NAME, "n2", "b", Tier.B)
    assert bundle.fields() == [NAME, PRICE]


def test_prompt_dict_merges_identical_values_and_keeps_best_tier():
    """Corroboration is worth recording; repeating the value wastes tokens."""
    bundle = CandidateBundle()
    bundle.add(CURRENCY, "USD", "JSON-LD offers.priceCurrency", Tier.A)
    bundle.add(CURRENCY, "USD", "microdata offer.priceCurrency", Tier.A)
    bundle.add(CURRENCY, "USD", "meta og:price:currency", Tier.B)

    entries = bundle.to_prompt_dict()[CURRENCY]
    assert len(entries) == 1
    assert entries[0]["tier"] == "A"
    assert entries[0]["source"].count(";") == 2


def test_prompt_dict_keeps_conflicting_values_separate():
    bundle = CandidateBundle()
    bundle.add(PRICE, 29.99, "JSON-LD", Tier.A)
    bundle.add(PRICE, 34.99, "text", Tier.D)
    entries = bundle.to_prompt_dict()[PRICE]
    assert [e["value"] for e in entries] == [29.99, 34.99]
    assert [e["tier"] for e in entries] == ["A", "D"]


def test_prompt_dict_is_json_safe_for_pydantic_values():
    bundle = CandidateBundle()
    bundle.add(
        VARIANTS,
        Variant(option_values=[VariantAttribute(name="Size", value="M")], sku="X"),
        "JSON-LD hasVariant",
        Tier.A,
    )
    value = bundle.to_prompt_dict()[VARIANTS][0]["value"]
    assert value == {"option_values": [{"name": "Size", "value": "M"}], "sku": "X", "image_urls": []}


def test_prompt_dict_truncates_long_values_but_bundle_keeps_them():
    """Truncation is a prompt-budget concern; grounding still needs the original."""
    long_text = "x" * 5000
    bundle = CandidateBundle()
    bundle.add(DESCRIPTION, long_text, "JSON-LD", Tier.A)

    entry = bundle.to_prompt_dict(max_chars=100)[DESCRIPTION][0]
    assert entry["truncated"] is True
    assert len(entry["value"]) == 101  # 100 chars plus the ellipsis
    assert bundle.best(DESCRIPTION).value == long_text


def test_prompt_dict_truncates_long_lists():
    bundle = CandidateBundle()
    bundle.add(IMAGE_URLS, [f"https://cdn.test/{i}.jpg" for i in range(100)], "a", Tier.A)
    entry = bundle.to_prompt_dict(max_items=10)[IMAGE_URLS][0]
    assert len(entry["value"]) == 10
    assert entry["truncated"] is True


# --- value coercion --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (29.99, 29.99),
        (30, 30.0),
        ("29.99", 29.99),
        ("$29.99", 29.99),
        ("29.99 USD", 29.99),
        ("USD 29.99", 29.99),
        ("\u00a318.99", 18.99),
        ("1,299.00", 1299.00),  # en grouping
        ("1.299,00", 1299.00),  # de grouping
        ("29,99", 29.99),  # comma as decimal separator
        ("1,299", 1299.0),  # comma as grouping, not two decimals
        ("1.234.567", 1234567.0),
        ("\u00a01 234,50", 1234.50),  # nbsp grouping
        ("-5.00", -5.00),
        (None, None),
        (True, None),  # bool is not a price
        ("", None),
        ("free", None),
        ([], None),
    ],
)
def test_coerce_number(raw, expected):
    assert coerce_number(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("one", ["one"]),
        (["a", "b"], ["a", "b"]),
        (None, []),
        ({"url": "https://cdn.test/a.jpg"}, ["https://cdn.test/a.jpg"]),
        ({"contentUrl": "https://cdn.test/b.jpg"}, ["https://cdn.test/b.jpg"]),
        ([{"url": "https://cdn.test/a.jpg"}, "https://cdn.test/b.jpg"], ["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"]),
        ({"unrelated": 1}, []),
        (12, ["12"]),
    ],
)
def test_coerce_text_list(raw, expected):
    assert coerce_text_list(raw) == expected
