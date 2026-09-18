"""Provenance scoring, the escalation trigger policy, and the second pass.

Scoring works backwards: each assembled value is matched against the candidates and
reported at the tier of whichever one it matches, falling back to E. Reporting the
strongest candidate's tier instead would credit provenance the model may not have
used, and this way a fabricated value shows up as E where E is impossible.

The second pass runs a stronger model over the whole page, so it is flag-gated.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from config import ESCALATION_MODEL
from extraction import images
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
    VIDEO_URL,
    Candidate,
    CandidateBundle,
    Tier,
    collapse,
)
from extraction.dom import semantic_html
from extraction.llm import LLMClient
from models import FactsResponse, Product

logger = logging.getLogger(__name__)

# Excludes image_urls (deterministic), category (its own validated descent) and
# description/key_features (always tier E, so replacing prose with prose is no repair).
_ESCALATABLE = (NAME, BRAND, PRICE, CURRENCY, COMPARE_AT_PRICE, COLORS, VIDEO_URL)

# Provenance strong enough to leave alone during a merge.
_TRUSTED = frozenset({Tier.A.value, Tier.B.value})

# Caps the escalation prompt at roughly $0.0025 per call; the largest page here
# serialises to about 85k characters.
_ESCALATION_CHARS = 40_000

_REASONING: dict[str, Any] = {"effort": "low"}


# --- scoring ---------------------------------------------------------------


def score_tiers(bundle: CandidateBundle, product: Product) -> dict[str, str]:
    """Work out which source each final value came from: {"price.price": "A", ...}.

    Takes each value in the finished product and searches the candidates for one that
    matches it. The tier of that candidate is the answer, or E if nothing matches,
    which means the model produced it without a source.
    """
    tiers: dict[str, str] = {}

    def score(field: str, value: Any) -> None:
        """Look up where one field's value came from and note its tier."""
        if value is None or value == [] or value == "":
            return
        candidate = _matching_candidate(bundle, field, value)
        tiers[field] = candidate.tier.value if candidate else Tier.E.value

    score(NAME, product.name)
    score(PRICE, product.price.price)
    score(CURRENCY, product.price.currency)
    score(COMPARE_AT_PRICE, product.price.compare_at_price)
    score(DESCRIPTION, product.description)
    score(KEY_FEATURES, product.key_features)
    score(IMAGE_URLS, product.image_urls)
    score(VIDEO_URL, product.video_url)
    score(BRAND, product.brand)
    score(COLORS, product.colors)
    score(VARIANTS, product.variants)

    return tiers


def _matching_candidate(
    bundle: CandidateBundle, field: str, value: Any
) -> Candidate | None:
    """Find the best-sourced candidate that this final value came from, or None."""
    matches: list[Candidate] = []

    for candidate in bundle.for_field(field):  # already ordered strongest first
        if _matches(field, value, candidate.value):
            matches.append(candidate)
            break

    # Colours and images are also carried inside variant candidates, so without
    # this the table reports E for values that came from declared data.
    if field in {COLORS, IMAGE_URLS}:
        for candidate in bundle.for_field(VARIANTS):
            if _matches_via_variant(field, value, candidate.value):
                matches.append(candidate)
                break

    if not matches:
        return None
    # Strongest wins across both routes.
    return min(matches, key=lambda candidate: candidate.tier.value)


def _matches_via_variant(field: str, value: Any, variant: Any) -> bool:
    """Look for a value inside a variant candidate.

    Colours and images also live within variants, so a colour found only there still
    has a real source rather than looking invented.
    """
    if field == IMAGE_URLS:
        return _image_match(value, getattr(variant, "image_urls", []) or [])
    axis_values = [pair.value for pair in getattr(variant, "option_values", [])]
    return _matches(field, value, axis_values)


def _matches(field: str, final: Any, candidate: Any) -> bool:
    """Is this final value the same thing as this candidate?

    Comparison depends on the type: prices to two decimal places, images through
    normalisation, text allowing for trimming, variants on their option pairs.
    """
    if field == IMAGE_URLS:
        return _image_match(final, candidate)
    if isinstance(final, (int, float)) and not isinstance(final, bool):
        return isinstance(candidate, (int, float)) and round(float(final), 2) == round(
            float(candidate), 2
        )
    if isinstance(final, str):
        return _text_match(final, candidate)
    if isinstance(final, list):
        return any(_matches(field, item, candidate) for item in final)
    if hasattr(final, "option_values"):
        return _variant_match(final, candidate)
    return False


def _text_match(final: str, candidate: Any) -> bool:
    """Do two strings match, allowing the final one to be a trimmed-down version?

    We asked the model to strip shop names and taglines off the product name, so the
    result should still count as coming from the title it was trimmed from.
    """
    if isinstance(candidate, list):
        return any(_text_match(final, item) for item in candidate)
    if not isinstance(candidate, str):
        return False
    left = _normalise_text(final)
    right = _normalise_text(candidate)
    if not left or not right:
        return False
    return left == right or (len(left) >= 3 and left in right)


def _normalise_text(value: str) -> str:
    """Lowercase and tidy whitespace, so two spellings of one value compare equal."""
    return collapse(value).casefold()


def _image_match(final: Any, candidate: Any) -> bool:
    """Do these image URLs match once both sides are normalised?

    Needed because harvesting rewrote the URLs to request full resolution, so they no
    longer look like what the page contained.
    """
    finals = final if isinstance(final, list) else [final]
    candidates = candidate if isinstance(candidate, list) else [candidate]
    normalised = {
        images.normalize(entry, None)
        for entry in candidates
        if isinstance(entry, str)
    }
    normalised.discard(None)
    return any(entry in normalised for entry in finals)


def _variant_match(final: Any, candidate: Any) -> bool:
    """Are these the same variant, i.e. exactly the same set of option pairs?"""
    if not hasattr(candidate, "option_values"):
        return False
    left = {(pair.name, pair.value) for pair in final.option_values}
    right = {(pair.name, pair.value) for pair in candidate.option_values}
    return bool(left) and left == right


# --- trigger policy --------------------------------------------------------


def needs_escalation(
    bundle: CandidateBundle,
    product: Product,
    tiers: dict[str, str],
    warnings: list[str],
    *,
    repairs: int = 0,
) -> list[str]:
    """List the reasons this page looks badly extracted. An empty list means it's fine.

    Checks whether the important fields have a real source: a price only found in
    visible text, a guessed currency, a brand taken from the shop's own name. Having
    no variants is deliberately not a reason, since plenty of products have none.
    """
    reasons: list[str] = []

    price_tier = tiers.get(PRICE)
    if price_tier is None:
        reasons.append("price has no provenance")
    elif price_tier >= Tier.D.value:
        reasons.append(f"price only at tier {price_tier}")

    currency = _matching_candidate(bundle, CURRENCY, product.price.currency)
    if currency is None:
        reasons.append("currency has no provenance")
    elif "inferred" in currency.source:
        # text.py writes the word deliberately, so this can read it.
        reasons.append("currency inferred rather than declared")

    brand = _matching_candidate(bundle, BRAND, product.brand)
    if brand is None:
        reasons.append("brand has no provenance")
    elif "site_name" in brand.source:
        # Where a retailer sells other people's products, the site name is the shop.
        reasons.append("brand taken from the site name")

    if not product.image_urls:
        # Emptiness only: a three-image gallery is a real gallery.
        reasons.append("no images")

    grounding_failures = [
        warning for warning in warnings if "not found in source" in warning
    ]
    if grounding_failures:
        reasons.append(f"{len(grounding_failures)} grounding failure(s)")

    if repairs:
        reasons.append(f"facts call needed {repairs} repair(s)")

    return reasons


# --- second pass -----------------------------------------------------------

_SYSTEM = """\
You are re-reading a product page that an earlier automated pass extracted poorly.
You are given the page itself, lightly stripped of styling and scripts, and the
fields that were weakly sourced.

Read the page and report the product's facts. Prefer machine-readable data in the
markup -- schema.org JSON-LD, itemprop attributes, OpenGraph meta tags -- over
rendered text, and rendered text over inference.

- name: the product's own name, without the shop's name or promotional wording.
- brand: who makes the product, not who sells it.
- price: what a shopper pays now, as a bare number.
- currency: ISO 4217, three uppercase letters. Use the page's language, domain and
  currency symbols to decide; do not default.
- compare_at_price: only a former price the page actually shows, and only when it
  exceeds price. A delivery threshold or a finance total is not a former price.
- colors: colour names offered for this product; empty if none.
- axes: leave empty. Variants are handled elsewhere.
- video_url: only a video URL, else null.
"""


async def escalate(
    soup: BeautifulSoup,
    product: Product,
    tiers: dict[str, str],
    reasons: list[str],
    client: LLMClient,
    *,
    model: str = ESCALATION_MODEL,
) -> FactsResponse | None:
    """Ask a stronger model to re-read the whole page. Returns its answer, or None.

    Runs at most once per page, never in a loop, and it is told which fields were
    weak so it knows where to concentrate.
    """

    weak = [field for field in _ESCALATABLE if tiers.get(field, Tier.E.value) not in _TRUSTED]
    excerpt = semantic_html(soup, max_chars=_ESCALATION_CHARS)

    body = (
        "Weakly sourced fields: " + (", ".join(weak) or "(none)") + "\n"
        "Why this page was flagged: " + "; ".join(reasons) + "\n\n"
        f"Current values:\n"
        f"  name: {product.name}\n"
        f"  brand: {product.brand}\n"
        f"  price: {product.price.price} {product.price.currency}\n\n"
        f"Page:\n{excerpt}"
    )

    result = await client.parse(
        model=model,
        input=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": body},
        ],
        text_format=FactsResponse,
        reasoning=_REASONING,
    )
    if result is None:
        logger.warning("escalation returned nothing; keeping the first-pass values")
    return result


def merge(
    product: Product, facts: FactsResponse, tiers: dict[str, str]
) -> tuple[Product, list[str]]:
    """Take the second model's answers, but only for fields that were weak before.

    Returns the updated product and a list describing what changed. Fields already
    sourced from the site's own product data are left alone, since replacing those
    would trade a declared price for a guess made from prose.
    """
    changes: list[str] = []
    updates: dict[str, Any] = {}

    def weak(field: str) -> bool:
        """True if this field did not come from the site's own product data."""
        return tiers.get(field, Tier.E.value) not in _TRUSTED

    if weak(NAME) and facts.name.strip() and facts.name.strip() != product.name:
        updates["name"] = facts.name.strip()
        changes.append(f"name: {product.name!r} -> {facts.name.strip()!r}")

    if weak(BRAND) and facts.brand.strip() and facts.brand.strip() != product.brand:
        updates["brand"] = facts.brand.strip()
        changes.append(f"brand: {product.brand!r} -> {facts.brand.strip()!r}")

    price = product.price.model_copy()
    price_changed = False
    if weak(PRICE) and facts.price > 0 and facts.price != price.price:
        changes.append(f"price: {price.price} -> {facts.price}")
        price.price = facts.price
        price_changed = True
    if weak(CURRENCY):
        currency = facts.currency.strip().upper()
        if len(currency) == 3 and currency.isalpha() and currency != price.currency:
            changes.append(f"currency: {price.currency} -> {currency}")
            price.currency = currency
            price_changed = True
    if weak(COMPARE_AT_PRICE) and facts.compare_at_price is not None:
        if facts.compare_at_price > price.price:
            changes.append(f"compare_at_price: -> {facts.compare_at_price}")
            price.compare_at_price = facts.compare_at_price
            price_changed = True
    if price_changed:
        updates["price"] = price

    if weak(COLORS) and facts.colors and facts.colors != product.colors:
        updates["colors"] = facts.colors
        changes.append(f"colors: {product.colors} -> {facts.colors}")

    if weak(VIDEO_URL) and facts.video_url and facts.video_url != product.video_url:
        updates["video_url"] = facts.video_url
        changes.append("video_url replaced")

    if not updates:
        return product, changes
    return product.model_copy(update=updates), changes
