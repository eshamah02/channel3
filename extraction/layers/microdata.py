"""HTML microdata carrying schema.org types. Tier A, same as JSON-LD.

https://html.spec.whatwg.org/multipage/microdata.html

Not redundant with jsonld.py: a page can ship zero JSON-LD and describe its price
entirely through itemprop attributes.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

from extraction.candidates import (
    BRAND,
    COLORS,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    LIST_FIELDS,
    NAME,
    PRICE,
    VIDEO_URL,
    CandidateBundle,
    Tier,
    coerce_number,
    local_name,
)

SOURCE = "microdata"

_PRODUCT_TYPES = frozenset({"product", "productgroup", "individualproduct"})
_OFFER_TYPES = frozenset(
    {
        "offer",
        "aggregateoffer",
        "pricespecification",
        "unitpricespecification",
        "compoundpricespecification",
    }
)
_BRAND_TYPES = frozenset({"brand", "organization", "corporation", "manufacturer"})

# itemprop -> field, keyed by the enclosing itemscope's type. Without that
# scoping, <span itemprop="name"> inside a Brand item becomes the product name.
_PRODUCT_PROPS: dict[str, str] = {
    "name": NAME,
    "description": DESCRIPTION,
    "brand": BRAND,
    "color": COLORS,
    "colour": COLORS,
    "image": IMAGE_URLS,
    "video": VIDEO_URL,
    # Permitted directly on the product when there is no separate Offer item.
    "price": PRICE,
    "pricecurrency": CURRENCY,
    "highprice": COMPARE_AT_PRICE,
    "lowprice": PRICE,
}

_OFFER_PROPS: dict[str, str] = {
    "price": PRICE,
    "pricecurrency": CURRENCY,
    "lowprice": PRICE,
    "highprice": COMPARE_AT_PRICE,
}

_BRAND_PROPS: dict[str, str] = {"name": BRAND}

# Collected when there is no usable itemscope. Restricted to pricing: an unscoped
# itemprop="price" is common shorthand, an unscoped "name" could be anything.
_UNSCOPED_PROPS: dict[str, str] = {
    "price": PRICE,
    "pricecurrency": CURRENCY,
    "lowprice": PRICE,
    "highprice": COMPARE_AT_PRICE,
}

_NUMERIC_FIELDS = frozenset({PRICE, COMPARE_AT_PRICE})


def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Read the page's microdata attributes and add what it finds as tier A.

    Microdata is the other way a site can state its product data: instead of a JSON
    block, it tags ordinary HTML with attributes like itemprop="price".
    """
    main_scope = _main_product_scope(soup)
    readings: list[_Reading] = []

    for element in soup.find_all(attrs={"itemprop": True}):
        if not isinstance(element, Tag):
            continue
        for prop in _property_names(element):
            reading = _read(element, prop, main_scope)
            if reading is not None:
                readings.append(reading)

    _emit(readings, bundle)


class _Reading:
    """One property we read off the page, with enough context to settle conflicts.

    Remembers whether it sat inside the main product, since a page can mark up several.
    """

    __slots__ = ("field", "value", "source", "in_product")

    def __init__(self, field: str, value: Any, source: str, in_product: bool) -> None:
        self.field = field
        self.value = value
        self.source = source
        self.in_product = in_product


def _property_names(element: Tag) -> list[str]:
    """The property names on one element. One element may declare several at once."""
    raw = element.get("itemprop") or ""
    if isinstance(raw, list):  # bs4 may pre-split multi-valued attributes
        tokens = raw
    else:
        tokens = raw.split()
    return [token.strip() for token in tokens if token.strip()]


def _scope_type(element: Tag | None) -> set[str]:
    """The schema.org types this element claims to describe, as lowercase names."""
    if element is None:
        return set()
    raw = element.get("itemtype") or ""
    values = raw if isinstance(raw, list) else raw.split()
    return {
        local_name(value).casefold()
        for value in values
        if value
    }


def _is_product_scope(element: Tag | None) -> bool:
    """True if this element is marked up as a product."""
    return bool(_scope_type(element) & _PRODUCT_TYPES)


def _main_product_scope(soup: BeautifulSoup) -> Tag | None:
    """Find which marked-up product on the page is the one actually being sold.

    A "you may also like" row marks up products too. The real one is whichever has the
    most properties, which is a structural test rather than a guess about layout.
    """
    scopes = [
        element
        for element in soup.find_all(attrs={"itemscope": True})
        if isinstance(element, Tag) and _is_product_scope(element)
    ]
    if not scopes:
        return None
    return max(scopes, key=lambda scope: len(scope.find_all(attrs={"itemprop": True})))


def _read(element: Tag, prop: str, main_scope: Tag | None) -> _Reading | None:
    """Read one property and work out which product it belongs to."""
    key = local_name(prop).casefold()

    # The nearest enclosing itemscope owns this property; an element with both
    # itemprop and itemscope is a nested item whose itemprop belongs to the parent.
    owner = element.find_parent(attrs={"itemscope": True})
    owner_types = _scope_type(owner)

    if owner is None:
        field = _UNSCOPED_PROPS.get(key)
        source = f"{SOURCE} unscoped itemprop={prop}"
        in_product = False
    else:
        if owner_types & _PRODUCT_TYPES:
            table = _PRODUCT_PROPS
        elif owner_types & _OFFER_TYPES:
            table = _OFFER_PROPS
        elif owner_types & _BRAND_TYPES:
            table = _BRAND_PROPS
        else:
            # Unrecognised item type: accept pricing only.
            table = _UNSCOPED_PROPS

        field = table.get(key)
        scope_label = "/".join(sorted(owner_types)) or "unscoped"
        source = f"{SOURCE} {scope_label}.{prop}"
        # Properties under the main product item outrank ones found elsewhere.
        in_product = main_scope is not None and (
            owner is main_scope or main_scope in owner.parents or owner in main_scope.parents
        )

    if field is None:
        return None

    value = _value_of(element)
    if value is None:
        return None

    if field in _NUMERIC_FIELDS:
        number = coerce_number(value)
        if number is None:
            return None
        value = number

    return _Reading(field=field, value=value, source=source, in_product=in_product)


def _value_of(element: Tag) -> str | None:
    """Get one property's value. Where it lives depends on the tag.

    The spec puts it in different places: `content` on <meta>, `href` on <a>, `src` on
    <img>, `datetime` on <time>, and the visible text on anything else.
    https://html.spec.whatwg.org/multipage/microdata.html#values
    """
    tag = element.name.lower()

    if tag == "meta":
        return _attr(element, "content")
    if tag in {"audio", "embed", "iframe", "img", "source", "track", "video"}:
        return _attr(element, "src")
    if tag in {"a", "area", "link"}:
        return _attr(element, "href")
    if tag == "object":
        return _attr(element, "data")
    if tag in {"data", "meter"}:
        return _attr(element, "value")
    if tag == "time":
        return _attr(element, "datetime") or element.get_text(" ", strip=True) or None

    # A nested item's text is the whole subtree concatenated, which is noise.
    if element.has_attr("itemscope"):
        return None

    # content is honoured on any element and carries the machine value.
    return _attr(element, "content") or element.get_text(" ", strip=True) or None


def _attr(element: Tag, name: str) -> str | None:
    """One attribute's value, tidied, or None if absent or blank."""
    value = element.get(name)
    if isinstance(value, list):
        value = " ".join(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _emit(readings: list[_Reading], bundle: CandidateBundle) -> None:
    """Send the readings to the bundle, one value per field.

    Fields holding a list keep every reading. Single-value fields prefer one from
    inside the main product, then whichever came first, and note that others existed.
    """
    by_field: dict[str, list[_Reading]] = {}
    for reading in readings:
        by_field.setdefault(reading.field, []).append(reading)

    for field, group in by_field.items():
        if field in LIST_FIELDS:
            for reading in group:
                bundle.add(field, reading.value, reading.source, Tier.A)
            continue

        distinct = {str(reading.value) for reading in group}
        preferred = [reading for reading in group if reading.in_product] or group
        chosen = preferred[0]

        source = chosen.source
        if len(distinct) > 1:
            source += f" (conflict: {len(distinct)} distinct values)"

        bundle.add(field, chosen.value, source, Tier.A)
