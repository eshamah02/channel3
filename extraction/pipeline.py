"""Orchestration: candidates, images, arbitration, assembly."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from bs4 import BeautifulSoup
from pydantic import ValidationError

from config import FACTS_MODEL, PROSE_MODEL
from extraction import category as category_module
from extraction import escalation, grounding, images
from extraction.candidates import COMPARE_AT_PRICE, VARIANTS, CandidateBundle, Tier
from extraction.category import CategoryError
from extraction.layers import embedded, jsonld, meta, microdata, pickers, text
from extraction.derive import content_hash, derive_colors, derive_options, make_id, make_slug
from extraction.dom import canonical_url, parse, semantic_html
from extraction import prompts
from extraction.llm import LLMClient
from models import (
    Category,
    ExtractedProduct,
    ExtractionMetadata,
    FactsResponse,
    Price,
    Product,
    ProseResponse,
    Variant,
    VariantAttribute,
    VariantAxis,
)

logger = logging.getLogger(__name__)

# Strongest first. Ordering is not required for correctness -- CandidateBundle
# sorts by tier -- but it makes first-seen tie-breaks favour the better source,
# and it makes a candidate dump read top-down from most to least trustworthy.
LAYERS = (jsonld, microdata, meta, embedded, text, pickers)

# Measured on this project: 7.3x cheaper on an identical prompt, and it removes the
# run-to-run variance in reasoning tokens that makes per-page cost unpredictable.
_REASONING: dict[str, Any] = {"effort": "low"}

# Roughly 3k tokens, which is where the description stops improving.
_PROSE_EXCERPT_CHARS = 12_000

# Variants at these tiers came from a declared matrix and are used verbatim.
_DECLARED_TIERS = (Tier.A, Tier.B)

_MAX_ATTEMPTS = 3

# A real taxonomy entry, used when category resolution is skipped or fails. It is
# deliberately a bare top-level node: obviously a placeholder rather than a
# plausible-looking wrong answer, and the metadata carries a warning saying so.
_PLACEHOLDER_CATEGORY = "Home & Garden"

# Distinguishes "use the real resolver" from "skip resolution entirely", since
# None has to keep meaning the latter for callers that want no model calls.
DEFAULT_RESOLVER = object()


class ExtractionError(RuntimeError):
    """Extraction could not produce a valid product for this page."""


def build_bundle(html: str) -> tuple[CandidateBundle, BeautifulSoup]:
    """Extract every candidate from one page.

    Returns the tree alongside the bundle because later stages need it too: image
    harvesting walks it, and the prose and escalation calls serialise it.
    """
    soup = parse(html)
    bundle = CandidateBundle()
    for layer in LAYERS:
        layer.extract(soup, bundle)
    return bundle, soup





def _facts_messages(
    bundle: CandidateBundle, *, variants_declared: bool
) -> list[dict[str, Any]]:
    system = prompts.FACTS_SYSTEM + (
        prompts.AXES_ALREADY_DECLARED if variants_declared else prompts.AXES_FROM_PICKERS
    )
    payload = bundle.to_prompt_dict()
    if variants_declared:
        # On a page with a large size run this is the majority of the bundle.
        payload.pop(VARIANTS, None)

    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Candidates:\n" + json.dumps(payload, ensure_ascii=False, indent=1),
        },
    ]


def _prose_messages(soup: BeautifulSoup, name: str, brand: str) -> list[dict[str, Any]]:
    excerpt = semantic_html(soup, max_chars=_PROSE_EXCERPT_CHARS)
    return [
        {"role": "system", "content": prompts.PROSE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Product: {name}\nBrand: {brand}\n\nPage excerpt:\n{excerpt}"
            ),
        },
    ]


# --- arbitration -----------------------------------------------------------


def _check_facts(facts: FactsResponse) -> None:
    """Semantic checks the JSON schema cannot express."""
    if not facts.name.strip():
        raise ValueError("name was empty; give the product's name.")
    if not facts.brand.strip():
        raise ValueError("brand was empty; name the maker of the product.")
    if facts.price <= 0:
        raise ValueError(f"price was {facts.price}; give the amount a shopper pays.")
    currency = facts.currency.strip()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(
            f"currency was {facts.currency!r}; use a three-letter ISO 4217 code."
        )


async def _facts(
    bundle: CandidateBundle, client: LLMClient, *, variants_declared: bool
) -> tuple[FactsResponse, int]:
    """Resolve the facts. Returns the response and how many repairs it needed.

    The repair count feeds the escalation policy.
    """
    messages = _facts_messages(bundle, variants_declared=variants_declared)
    complaint: str | None = None

    for attempt in range(_MAX_ATTEMPTS):
        turn = messages if complaint is None else messages + [
            {
                "role": "user",
                "content": (
                    f"That response was rejected: {complaint}\n"
                    "Return the whole object again with that field corrected."
                ),
            }
        ]
        result = await client.parse(
            model=FACTS_MODEL,
            input=turn,
            text_format=FactsResponse,
            reasoning=_REASONING,
        )

        if result is None:
            complaint = "the response was empty or refused"
            logger.debug("facts attempt %d: empty response", attempt + 1)
            continue

        try:
            _check_facts(result)
        except ValueError as exc:
            complaint = str(exc)
            logger.debug("facts attempt %d rejected: %s", attempt + 1, complaint)
            continue

        return result, attempt

    raise ExtractionError(f"facts call failed after {_MAX_ATTEMPTS} attempts: {complaint}")


async def _prose(
    soup: BeautifulSoup, facts: FactsResponse, client: LLMClient
) -> ProseResponse:
    result = await client.parse(
        model=PROSE_MODEL,
        input=_prose_messages(soup, facts.name, facts.brand),
        text_format=ProseResponse,
        reasoning=_REASONING,
    )
    if result is None:
        # Synthesis, so a page is still servable without it.
        logger.warning("prose call returned nothing; falling back to empty copy")
        return ProseResponse(description="", key_features=[])
    return result


# --- assembly --------------------------------------------------------------


def _declared_variants(bundle: CandidateBundle) -> list[Variant]:
    """Variants from a machine-readable statement of the matrix, if any."""
    candidates = bundle.all_at_best_tier(VARIANTS)
    if candidates and candidates[0].tier in _DECLARED_TIERS:
        return [candidate.value for candidate in candidates]
    return []


def _variants_from_axes(axes: list[VariantAxis]) -> list[Variant]:
    """One single-axis Variant per observed value.

    A picker reveals the axes, not the combinations, so none is invented here.
    """
    out: list[Variant] = []
    for axis in axes:
        name = (axis.name or "").strip()
        if not name:
            continue
        for value in axis.values:
            cleaned = (value or "").strip()
            if cleaned:
                out.append(
                    Variant(
                        option_values=[VariantAttribute(name=name, value=cleaned)]
                    )
                )
    return out


def _price_of(
    facts: FactsResponse, bundle: CandidateBundle, warnings: list[str]
) -> Price:
    """Build the Price, with compare_at_price grounded in the candidates.

    A compare-at at or below the price is a discount of nothing. And it must match a
    candidate offered for that field: observed on a real page, the model promoted a
    free-delivery threshold into a former price and invented a saving. Missing a
    genuine sale is the smaller error.
    """
    compare_at = facts.compare_at_price
    if compare_at is None:
        return Price(price=facts.price, currency=facts.currency.strip().upper())

    if compare_at <= facts.price:
        warnings.append(
            f"dropped compare_at_price {compare_at}: not above price {facts.price}"
        )
        compare_at = None
    else:
        supported = {
            round(float(candidate.value), 2)
            for candidate in bundle.for_field(COMPARE_AT_PRICE)
            if isinstance(candidate.value, (int, float))
        }
        if round(compare_at, 2) not in supported:
            warnings.append(
                f"dropped compare_at_price {compare_at}: no candidate for that field"
            )
            compare_at = None

    return Price(
        price=facts.price,
        currency=facts.currency.strip().upper(),
        compare_at_price=compare_at,
    )


async def extract(
    html: str,
    source_file: str,
    client: LLMClient,
    *,
    category_resolver: Callable[[str, str, LLMClient], Awaitable[Category]]
    | None
    | Any = DEFAULT_RESOLVER,
    escalate: bool = False,
) -> ExtractedProduct:
    """Turn one page's HTML into a validated ExtractedProduct.

    category_resolver defaults to the taxonomy descent; pass None to substitute a
    flagged placeholder. Scoring and grounding always run because they are free,
    but the escalation pass costs several times a normal extraction, so it is opt-in.
    """
    first_call = len(client.records)
    warnings: list[str] = []

    bundle, soup = build_bundle(html)
    base_url = canonical_url(soup)
    image_urls = images.harvest(soup, bundle, base_url)
    if not image_urls:
        warnings.append("no images harvested")

    declared = _declared_variants(bundle)
    facts, repairs = await _facts(bundle, client, variants_declared=bool(declared))
    prose = await _prose(soup, facts, client)

    variants = declared or _variants_from_axes(facts.axes)
    if declared and facts.axes:
        # Ignoring a stray list is cheaper than another round trip.
        warnings.append("ignored model-supplied axes in favour of declared variants")

    if category_resolver is DEFAULT_RESOLVER:
        category_resolver = category_module.resolve_category

    if category_resolver is None:
        category = Category(name=_PLACEHOLDER_CATEGORY)
        warnings.append(
            f"category not resolved; placeholder {_PLACEHOLDER_CATEGORY!r} used"
        )
    else:
        try:
            category = await category_resolver(facts.name, prose.description, client)
        except (CategoryError, ValidationError) as exc:
            # One page must not abort a batch, and the warning keeps the fallback
            # from looking authoritative.
            logger.warning("category resolution failed for %r: %s", facts.name, exc)
            category = Category(name=_PLACEHOLDER_CATEGORY)
            warnings.append(f"category resolution failed ({exc}); placeholder used")

    try:
        product = Product(
            name=facts.name.strip(),
            price=_price_of(facts, bundle, warnings),
            description=prose.description.strip(),
            key_features=[item.strip() for item in prose.key_features if item.strip()],
            image_urls=image_urls,
            video_url=facts.video_url,
            category=category,
            brand=facts.brand.strip(),
            colors=derive_colors(variants, facts.colors),
            variants=variants,
        )
    except ValidationError as exc:
        raise ExtractionError(f"assembled product did not validate: {exc}") from exc

    # Grounding: does each value actually appear in the bytes we were given?
    # Only URLs and the brand are checked -- see grounding.py for why a price
    # substring test cannot fail and so is not worth running.
    warnings.extend(grounding.verify_urls(product.image_urls, html))
    warnings.extend(grounding.verify_text(product.brand, html, label="brand"))

    tiers = escalation.score_tiers(bundle, product)
    reasons = escalation.needs_escalation(
        bundle, product, tiers, warnings, repairs=repairs
    )
    escalated = False

    if reasons:
        warnings.append("weak extraction: " + "; ".join(reasons))

    if reasons and escalate:
        facts_again = await escalation.escalate(soup, product, tiers, reasons, client)
        if facts_again is not None:
            product, changes = escalation.merge(product, facts_again, tiers)
            escalated = True
            if changes:
                warnings.append("escalation changed: " + "; ".join(changes))
            else:
                warnings.append("escalation confirmed the first pass")
            # A replaced value may now match a stronger candidate, or none at all.
            tiers = escalation.score_tiers(bundle, product)

    identity_url = base_url or f"file://{source_file}"
    if base_url is None:
        warnings.append("no canonical URL; id derived from the input filename")

    return ExtractedProduct(
        id=make_id(identity_url),
        slug=make_slug(product.name),
        source_url=base_url,
        source_file=source_file,
        content_hash=content_hash(html),
        options=derive_options(variants),
        product=product,
        extraction=ExtractionMetadata(
            field_tiers=tiers,
            escalated=escalated,
            warnings=warnings,
            cost_usd=client.cost_since(first_call),
        ),
    )

