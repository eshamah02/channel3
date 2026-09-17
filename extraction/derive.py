"""Pure derivations over extracted data. No I/O, no model calls, no HTML.

Two jobs. First, turn a flat list of variants into the selectable axes a
storefront renders, so the axes can never disagree with the variants they came
from. Second, mint the identity fields on the envelope.

Everything here is a deterministic function of its arguments, which is why the
whole variant design can be proven by tests before any HTML is parsed or any
token is spent.
"""

import hashlib
import re
import unicodedata

from models import Variant, VariantOption

# A slug is cosmetic -- ExtractedProduct.id is the routing key -- so this is
# only a floor on readability, not a uniqueness guarantee.
_MAX_SLUG_LENGTH = 80

# Labels that mean "this axis is the colour axis". A two-word English/en-GB
# check, used only as a fallback when the model returned no colours. It reads a
# human-facing label, not a site's markup, so it carries no site-specific
# knowledge -- but it is a language assumption, and on a non-English page the
# fallback simply yields nothing rather than guessing wrong.
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

    First-seen order is preserved deliberately, for both axes and values. Pages
    list sizes semantically -- S, M, L or US 6 through 13 -- and that ordering
    is not recoverable by sorting, so inheriting the page's order gives correct
    pickers for free while sorting would actively destroy the information.

    Sparse matrices are handled by construction: the union of values per axis is
    collected independently, so Black/S, Black/M, White/S yields Color=[Black,
    White] and Size=[S, M] even though White/M is never offered. Task 12 renders
    that gap as a disabled option rather than hiding it.
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

    The model's list wins when it produced one, because a page often names
    colours in prose ("Heather Grey") that never appear as a variant axis. The
    axis is only consulted when the model gave nothing, which means the two
    fields are always either the same claim or one is empty -- never a conflict
    a client would have to reconcile.
    """
    if llm_colors:
        return _dedupe(llm_colors)

    for option in derive_options(variants):
        if option.name.strip().casefold() in _COLOR_AXIS_LABELS:
            return option.values

    return []


def make_id(canonical_url: str) -> str:
    """Stable content-addressed id: truncated sha256 of the canonical URL.

    Hashing the URL rather than the page body means re-crawling an edited page
    produces the same id, so caches and any downstream references survive. 16
    hex chars is 64 bits -- ample for a catalogue, and short enough to read in a
    filename or a URL path.
    """
    normalized = canonical_url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def make_slug(name: str) -> str:
    """Human-readable URL fragment. Cosmetic; ids do the routing.

    Accents are folded to their base letters via NFKD so "Café" becomes "cafe",
    while scripts without an ASCII decomposition (CJK, Cyrillic) are kept as-is
    rather than silently deleted -- percent-encoding handles them, and dropping
    them would turn every such name into the same empty slug.
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
