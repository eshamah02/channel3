"""Pure derivations over extracted data. No I/O, no model calls, no HTML.

Turns a flat variant list into the selectable axes a storefront renders, so the two
can never disagree, and mints the envelope's identity fields.
"""

import hashlib
import re
import unicodedata

from models import Variant, VariantOption

# A slug is cosmetic -- ExtractedProduct.id is the routing key -- so this is
# only a floor on readability, not a uniqueness guarantee.
_MAX_SLUG_LENGTH = 80

# A language assumption, not a site-specific one: it reads a human-facing label, and
# on a non-English page the fallback yields nothing rather than guessing wrong.
_COLOR_AXIS_LABELS = frozenset({"color", "colour"})


def _dedupe(values: list[str]) -> list[str]:
    """Drop repeats while keeping the first occurrence's position and casing.

    Matched case-insensitively on stripped text so "Black" and "black " from two
    different variants collapse, but the first spelling seen is what survives.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def derive_options(variants: list[Variant]) -> list[VariantOption]:
    """Group variant attribute values into per-axis option lists.

    First-seen order is preserved because pages list sizes semantically (S, M, L)
    and sorting would destroy that. Values are unioned per axis independently, so a
    sparse matrix still advertises every value that exists.
    """
    grouped: dict[str, list[str]] = {}
    # dict preserves insertion order, so axis order follows first appearance.
    for variant in variants:
        for attribute in variant.option_values:
            axis = attribute.name.strip()
            if not axis or not attribute.value.strip():
                continue
            grouped.setdefault(axis, []).append(attribute.value)

    return [
        VariantOption(name=axis, values=_dedupe(values))
        for axis, values in grouped.items()
    ]


def derive_colors(variants: list[Variant], llm_colors: list[str] | None = None) -> list[str]:
    """Resolve Product.colors so it can never contradict Product.variants.

    The model's list wins because pages name colours in prose that never appear as an
    axis value; the axis is consulted only when the model gave nothing.
    """
    if llm_colors:
        return _dedupe(llm_colors)

    for option in derive_options(variants):
        if option.name.strip().casefold() in _COLOR_AXIS_LABELS:
            return option.values

    return []


def make_id(canonical_url: str) -> str:
    """Stable content-addressed id: truncated sha256 of the canonical URL.

    Hashing the URL rather than the body means an edited page keeps its id, so
    caches and downstream references survive a re-crawl.
    """
    normalized = canonical_url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def make_slug(name: str) -> str:
    """Human-readable URL fragment. Cosmetic; ids do the routing.

    Accents fold to base letters via NFKD, but CJK and Cyrillic are kept rather than
    deleted, since dropping them would collapse every such name to the same slug.
    """
    # NFKD splits "é" into "e" + combining accent; Mn is the combining-mark
    # category, so filtering it leaves the base letter behind.
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))

    kept = [c.lower() if c.isalnum() else "-" for c in stripped]
    slug = re.sub(r"-+", "-", "".join(kept)).strip("-")

    if len(slug) > _MAX_SLUG_LENGTH:
        slug = slug[:_MAX_SLUG_LENGTH].rstrip("-")
        # Prefer cutting at a word boundary, but not if that guts the slug.
        if "-" in slug and slug.rindex("-") > _MAX_SLUG_LENGTH // 2:
            slug = slug[: slug.rindex("-")]

    return slug or "product"


def content_hash(raw_html: str) -> str:
    """sha256 of the raw input, the cache key for "has this page changed?".

    Full digest rather than truncated: this one is compared for equality to
    decide whether to spend tokens, so there is no reason to trade away
    collision resistance for brevity.
    """
    return hashlib.sha256(raw_html.encode("utf-8", errors="replace")).hexdigest()
