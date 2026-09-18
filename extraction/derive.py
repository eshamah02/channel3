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
    """Remove duplicates from a list of strings, keeping the original order.

    Compares case-insensitively so "Black" and "black " count as the same, and keeps
    whichever spelling appeared first.
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
    """List the choices a shopper can make, worked out from the variants.

    Given variants like Black/S, Black/M and White/S, returns Color=[Black, White]
    and Size=[S, M]. Page order is kept, because pages list sizes small to large and
    sorting alphabetically would ruin that. Each axis is collected independently, so
    a patchy set of variants still advertises every value that exists somewhere.
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
    """The product's colours, preferring the model's list and falling back to variants.

    The model wins because pages mention colours in prose that never appear as a
    picker option. Falling back to the colour axis means this can never disagree with
    the variants.
    """
    if llm_colors:
        return _dedupe(llm_colors)

    for option in derive_options(variants):
        if option.name.strip().casefold() in _COLOR_AXIS_LABELS:
            return option.values

    return []


def make_id(canonical_url: str) -> str:
    """The product's id: the first 16 characters of the URL's sha256 hash.

    Hashing the URL rather than the page contents means an edited page keeps the same
    id, so links and caches still work after a re-crawl.
    """
    normalized = canonical_url.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def make_slug(name: str) -> str:
    """Turn a product name into a URL-friendly string: "Café Chair" -> "cafe-chair".

    Decorative only; the id is what actually identifies a product. Accented letters
    fold to their base letter, but Chinese and Cyrillic characters are kept rather
    than stripped, since removing them would turn every such name into the same slug.
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
    """Fingerprint of the raw HTML, used to answer "has this page changed?".

    The full hash rather than a shortened one, because a match here decides whether
    to skip the page and spend nothing, so a collision would mean stale data.
    """
    return hashlib.sha256(raw_html.encode("utf-8", errors="replace")).hexdigest()
