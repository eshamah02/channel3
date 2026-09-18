"""Taxonomy descent.

The whole design exists to guarantee one invariant: whatever comes back is in
categories.txt. Several tests here assert that directly, across scripted descents
that stop early, run to a leaf, and misbehave.
"""

import pytest

from extraction import category
from extraction.category import (
    SEPARATOR,
    CategoryError,
    children_of,
    resolve_category,
)
from extraction.llm import FakeLLMClient
from models import CATEGORIES_FILE, VALID_CATEGORIES, Category, CategoryChoice


def prompt_text(client) -> str:
    """Every message body the client was sent, for asserting on prompt content."""
    return "\n".join(
        message["content"]
        for call in client.calls
        for message in call["input"]
        if isinstance(message.get("content"), str)
    )


def choices(*numbers: int) -> list[CategoryChoice]:
    return [CategoryChoice(choice=number) for number in numbers]


def shallow_second_pass(excluded_root: str) -> list[CategoryChoice]:
    """A retry that picks the first available root and immediately declines it.

    Used by tests whose first pass ends in a stop, which now earns a second pass.
    This one is deliberately worse than any first result, so the first is kept.
    """
    remaining = [root for root in children_of() if root != excluded_root]
    first = remaining[0]
    return choices(1, len(children_of([first])) + 1)


def entries() -> list[str]:
    """Direct line scan of the file, independent of the tree builder."""
    with open(CATEGORIES_FILE, encoding="utf-8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#")
        ]


# --- the tree --------------------------------------------------------------


def test_top_level_matches_a_direct_line_scan():
    expected = [entry for entry in entries() if SEPARATOR not in entry]
    assert children_of() == expected
    assert len(children_of()) == 21


def test_version_comment_is_not_a_category():
    assert not any(name.startswith("#") for name in children_of())


def test_children_of_a_known_node():
    assert children_of(["Apparel & Accessories"]) == [
        "Clothing",
        "Clothing Accessories",
        "Costumes & Accessories",
        "Handbag & Wallet Accessories",
        "Handbags, Wallets & Cases",
        "Jewelry",
        "Shoe Accessories",
        "Shoes",
    ]


def test_children_counts_match_the_file():
    """Every node's child count equals the distinct next segments in the file."""
    for parent in ("Animals & Pet Supplies", "Hardware", "Home & Garden"):
        prefix = parent + SEPARATOR
        expected = {
            entry[len(prefix) :].split(SEPARATOR)[0]
            for entry in entries()
            if entry.startswith(prefix)
        }
        assert set(children_of([parent])) == expected, parent


def test_leaf_has_no_children():
    assert children_of(["Apparel & Accessories", "Clothing", "Pants"]) == []


def test_unknown_path_yields_no_children():
    assert children_of(["Not A Category"]) == []
    assert children_of(["Hardware", "Not A Child"]) == []


def test_every_tree_path_is_a_valid_category():
    """Walk the whole tree and confirm each assembled path is in the file.

    This is the property the descent relies on: offering only real children cannot
    produce an invalid path.
    """
    def walk(node, path):
        for name, child in node.items():
            here = path + [name]
            assert SEPARATOR.join(here) in VALID_CATEGORIES
            walk(child, here)

    walk(category.TREE, [])


# --- descent ---------------------------------------------------------------


async def test_descent_to_a_leaf():
    """Apparel & Accessories (2) -> Clothing (1) -> Pants (7)."""
    client = FakeLLMClient(choices(2, 1, 7))
    result = await resolve_category("Miller Trousers", "Tailored cotton trousers.", client)

    assert result.name == "Apparel & Accessories > Clothing > Pants"
    assert result.name in VALID_CATEGORIES


async def test_descent_stops_when_the_model_declines_to_go_deeper():
    """The stop option is the last index, offered from level two onward."""
    stop_at_clothing = len(children_of(["Apparel & Accessories", "Clothing"])) + 1
    client = FakeLLMClient(
        choices(2, 1, stop_at_clothing)
        + shallow_second_pass("Apparel & Accessories")
    )
    result = await resolve_category("Something apparel", "Clothing of some kind.", client)

    assert result.name == "Apparel & Accessories > Clothing"
    assert result.name in VALID_CATEGORIES


async def test_no_stop_option_at_the_top_level():
    """There is no empty category, so the product must land in one of the 21."""
    client = FakeLLMClient(choices(1, 1, 1, 1, 1, 1, 1, 1))
    await resolve_category("Anything", "Anything at all.", client)

    first_prompt = client.calls[0]["input"][-1]["content"]
    assert "stop at" not in first_prompt
    assert "(top level)" in first_prompt


async def test_stop_option_appears_below_the_top_level():
    client = FakeLLMClient(choices(2, *([1] * 8)))
    await resolve_category("Anything", "Anything at all.", client)
    assert 'stop at "Apparel & Accessories"' in client.calls[1]["input"][-1]["content"]


async def test_descent_terminates_at_a_leaf_without_further_calls():
    client = FakeLLMClient(choices(2, 1, 7))
    await resolve_category("Trousers", "Trousers.", client)
    # Pants has no children, so the loop stops rather than asking again.
    assert len(client.calls) == 3


async def test_out_of_range_triggers_one_retry_then_stops_validly():
    stop_at_clothing = len(children_of(["Apparel & Accessories", "Clothing"])) + 1
    client = FakeLLMClient(
        choices(2, 999, 1, stop_at_clothing)
        + shallow_second_pass("Apparel & Accessories")
    )
    result = await resolve_category("Shirt", "A shirt.", client)

    assert result.name == "Apparel & Accessories > Clothing"
    assert result.name in VALID_CATEGORIES
    assert "not one of the options" in client.calls[2]["input"][-1]["content"]


async def test_two_bad_choices_stop_at_the_current_node():
    client = FakeLLMClient(
        choices(2, 1, 999, 999) + shallow_second_pass("Apparel & Accessories")
    )
    result = await resolve_category("Mystery", "Unclear.", client)
    assert result.name == "Apparel & Accessories > Clothing"
    assert result.name in VALID_CATEGORIES


async def test_zero_and_negative_choices_are_out_of_range():
    client = FakeLLMClient(
        choices(2, 1, 0, -1) + shallow_second_pass("Apparel & Accessories")
    )
    result = await resolve_category("Mystery", "Unclear.", client)
    assert result.name == "Apparel & Accessories > Clothing"


# --- recovery from a wrong root -------------------------------------------


def pick(options: list[str], name: str) -> int:
    """1-based index of an option, as the prompt numbers them."""
    return options.index(name) + 1


def options_shown(client: FakeLLMClient, call: int) -> list[str]:
    """The option labels presented in one call, stripped of their numbering."""
    body = client.calls[call]["input"][-1]["content"]
    lines = body.split("Options:\n")[1].splitlines()
    return [line.split(". ", 1)[1] for line in lines if ". " in line]


async def test_a_depth_one_result_gets_a_second_attempt_from_another_root():
    """Observed live: a floor lamp landed in "Furniture" and stopped there.

    Furniture's children are seating, tables and storage, so stopping was the right
    move given that root -- but "Home & Garden > Lighting > Lamps" exists. Stopping
    at depth one is far more often a wrong root than a genuinely un-specific
    product, so it earns one retry from a different root.
    """
    roots = children_of()
    retry_roots = [root for root in roots if root != "Furniture"]

    client = FakeLLMClient(
        # First pass: pick Furniture, then find nothing fitting among its children.
        choices(pick(roots, "Furniture"), len(children_of(["Furniture"])) + 1)
        # Second pass: Furniture is no longer on offer, so the numbering shifts.
        + choices(pick(retry_roots, "Home & Garden"))
        + choices(pick(children_of(["Home & Garden"]), "Lighting"))
        + choices(pick(children_of(["Home & Garden", "Lighting"]), "Lamps"))
    )
    result = await resolve_category("Pilar Floor Lamp", "A tall floor lamp.", client)

    assert result.name == "Home & Garden > Lighting > Lamps"


async def test_a_leaf_result_beats_a_stopped_one():
    """The signal that generalises the retry, from a real misclassification.

    A sneaker was placed in "Sporting Goods > Athletics > Basketball", whose four
    children are hoops, balls and training aids -- so the model declined all of
    them. "Apparel & Accessories > Shoes" is a leaf. Running out of children means
    the taxonomy has nothing more specific; declining every child means the branch
    was probably wrong.
    """
    roots = children_of()
    sporting = children_of(["Sporting Goods"])
    athletics = children_of(["Sporting Goods", "Athletics"])
    retry_roots = [root for root in roots if root != "Sporting Goods"]

    client = FakeLLMClient(
        # First pass: down to Basketball, then decline all four of its children.
        choices(
            pick(roots, "Sporting Goods"),
            pick(sporting, "Athletics"),
            pick(athletics, "Basketball"),
            len(children_of(["Sporting Goods", "Athletics", "Basketball"])) + 1,
        )
        # Second pass: Shoes, which is a leaf.
        + choices(
            pick(retry_roots, "Apparel & Accessories"),
            pick(children_of(["Apparel & Accessories"]), "Shoes"),
        )
    )
    result = await resolve_category("Air Force Sneaker", "A leather lifestyle sneaker.", client)

    assert result.name == "Apparel & Accessories > Shoes"


async def test_a_leaf_on_the_first_pass_never_retries():
    """Cost is self-limiting: the common case pays nothing extra."""
    client = FakeLLMClient(
        choices(2, pick(children_of(["Apparel & Accessories"]), "Shoes"))
    )
    result = await resolve_category("Sneaker", "A shoe.", client)

    assert result.name == "Apparel & Accessories > Shoes"
    assert len(client.calls) == 2


async def test_a_deeper_stop_beats_a_shallower_one():
    """When neither pass reaches a leaf, specificity decides."""
    roots = children_of()
    retry_roots = [root for root in roots if root != "Apparel & Accessories"]

    client = FakeLLMClient(
        # First pass stops at depth one.
        choices(pick(roots, "Apparel & Accessories"), len(children_of(["Apparel & Accessories"])) + 1)
        # Second pass stops at depth two.
        + choices(
            pick(retry_roots, "Home & Garden"),
            pick(children_of(["Home & Garden"]), "Lighting"),
            len(children_of(["Home & Garden", "Lighting"])) + 1,
        )
    )
    result = await resolve_category("Thing", "A thing.", client)

    assert result.name == "Home & Garden > Lighting"


async def test_the_retry_cannot_choose_the_same_root_again():
    roots = children_of()
    client = FakeLLMClient(
        choices(pick(roots, "Furniture"), len(children_of(["Furniture"])) + 1)
        + choices(*([1] * 8))
    )
    await resolve_category("Lamp", "A lamp.", client)

    retry_options = options_shown(client, 2)
    assert "Furniture" not in retry_options
    assert len(retry_options) == len(roots) - 1


async def test_the_first_result_is_kept_when_the_retry_is_no_better():
    """A product that really is only broadly classifiable keeps its broad answer."""
    stop_at_apparel = len(children_of(["Apparel & Accessories"])) + 1
    second_root = 1
    stop_at_second = len(children_of([children_of()[0]])) + 1

    client = FakeLLMClient(choices(2, stop_at_apparel, second_root, stop_at_second))
    result = await resolve_category("Mystery", "Unclear.", client)

    assert result.name == "Apparel & Accessories"
    assert result.name in VALID_CATEGORIES


async def test_the_retry_happens_only_once():
    stop_at_apparel = len(children_of(["Apparel & Accessories"])) + 1
    stop_at_first = len(children_of([children_of()[0]])) + 1
    client = FakeLLMClient(choices(2, stop_at_apparel, 1, stop_at_first))

    result = await resolve_category("Mystery", "Unclear.", client)

    assert result.name in VALID_CATEGORIES
    assert len(client.calls) == 4  # two passes, two calls each, no third pass


async def test_refusal_at_the_top_level_raises():
    """Nothing valid can be assembled, so the caller decides what to do."""
    client = FakeLLMClient([None, None])
    with pytest.raises(CategoryError, match="no top-level category"):
        await resolve_category("Mystery", "Unclear.", client)


async def test_a_refusal_is_retried():
    client = FakeLLMClient([None, *choices(2), *choices(1), *choices(7)])
    result = await resolve_category("Trousers", "Trousers.", client)
    assert result.name == "Apparel & Accessories > Clothing > Pants"


async def test_depth_guard_bounds_the_descent():
    """Always choosing option 1 must terminate, not loop."""
    client = FakeLLMClient(choices(*([1] * 20)))
    result = await resolve_category("Anything", "Anything.", client)
    assert result.name in VALID_CATEGORIES
    assert len(client.calls) <= category._MAX_DEPTH


@pytest.mark.parametrize(
    "sequence",
    [
        (1,),
        (2, 1, 7),
        (5, 2),
        (10, 3, 2, 1),
        (21, 1),
        (2, 9),  # a stop at level two
        (3, 999, 1),  # a repair mid-descent
    ],
)
async def test_every_scripted_descent_returns_a_valid_category(sequence):
    client = FakeLLMClient(choices(*sequence) + choices(*([1] * 8)))
    result = await resolve_category("Probe", "A product of some kind.", client)
    assert result.name in VALID_CATEGORIES
    Category(name=result.name)  # the model's own validator


# --- prompt content -------------------------------------------------------


async def test_prompt_carries_the_name_description_and_position():
    client = FakeLLMClient(choices(2, 1, 7))
    await resolve_category("Miller Trousers", "Tailored cotton trousers.", client)

    body = client.calls[1]["input"][-1]["content"]
    assert "Product: Miller Trousers" in body
    assert "Tailored cotton trousers." in body
    assert "Current position: Apparel & Accessories" in body


async def test_description_is_trimmed_so_it_does_not_dominate_repeated_calls():
    client = FakeLLMClient(choices(2, 1, 7))
    await resolve_category("Widget", "x" * 5000, client)
    body = client.calls[0]["input"][-1]["content"]
    assert body.count("x") == category._DESCRIPTION_CHARS


async def test_options_are_numbered_from_one():
    client = FakeLLMClient(choices(*([1] * 8)))
    await resolve_category("Widget", "A widget.", client)
    body = client.calls[0]["input"][-1]["content"]
    assert "1. Animals & Pet Supplies" in body
    assert "21. Vehicles & Parts" in body


async def test_reasoning_effort_is_pinned_low():
    client = FakeLLMClient(choices(*([1] * 8)))
    await resolve_category("Widget", "A widget.", client)
    assert client.calls[0]["kwargs"]["reasoning"] == {"effort": "low"}


async def test_prompt_names_no_site_from_the_dataset():
    client = FakeLLMClient(choices(2, 1, 7))
    await resolve_category("Trousers", "Trousers.", client)
    sent = prompt_text(client).casefold()
    for banned in ("nike", "llbean", "adaysmarch", "acehardware", "dewalt"):
        assert banned not in sent
