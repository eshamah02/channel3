"""Client-side application state, located by JSON shape. Tier C.

Hydration payloads often carry the product record more precisely than the rendered
text, but nothing in them is declared to mean anything, so the semantics have to be
inferred and this ranks below the standards-based layers.

The key vocabulary below is a list of published commerce conventions, not keys
observed in any particular page. Discovery is by shape -- a dict with a name-like
key and either a price or an identifier -- so nothing names a site, a framework or
a global variable, and a page inventing its own key names is missed rather than
guessed at.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from bs4 import BeautifulSoup, Tag

from extraction.candidates import (
    BRAND,
    COLORS,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    NAME,
    PRICE,
    CandidateBundle,
    Tier,
    coerce_number,
    coerce_text_list,
)

logger = logging.getLogger(__name__)

SOURCE = "embedded JSON"

# Matched case-insensitively with underscores removed, so "sale_price",
# "salePrice" and "SalePrice" all hit one entry.
_NAME_KEYS = frozenset({"name", "title", "productname", "displayname", "producttitle"})
_PRICE_KEYS = frozenset(
    {
        "price",
        "saleprice",
        "currentprice",
        "finalprice",
        "offerprice",
        "unitprice",
        "amount",
        "pricevalue",
    }
)
_COMPARE_KEYS = frozenset(
    {
        "compareatprice",
        "originalprice",
        "listprice",
        "wasprice",
        "regularprice",
        "retailprice",
        "msrp",
        "fullprice",
    }
)
_CURRENCY_KEYS = frozenset(
    {"currency", "currencycode", "pricecurrency", "currencyisocode"}
)
_ID_KEYS = frozenset({"sku", "skuid", "productid", "itemid", "partnumber", "mpn"})
_DESCRIPTION_KEYS = frozenset(
    {"description", "shortdescription", "longdescription", "productdescription"}
)
_BRAND_KEYS = frozenset({"brand", "brandname", "manufacturer", "vendor"})
_IMAGE_KEYS = frozenset({"image", "images", "imageurl", "imageurls", "mainimage"})
_COLOR_KEYS = frozenset({"color", "colour", "colorname", "colourname"})

# Bounds on the search: a several-hundred-kilobyte state blob would otherwise
# dominate the pipeline's runtime, and a product record is never buried that deep.
_MAX_DEPTH = 12
_MAX_NODES = 200_000

_MIN_ATTR_JSON_LENGTH = 40


def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Find the product data a JavaScript site left in the page, and add it as tier C.

    Sites built with React and similar frameworks embed a blob of JSON to build the
    page from. It often holds exact prices, but nothing in it is *declared* to mean
    anything, so we infer meaning from key names and rank it below the standards.
    """
    best: dict[str, Any] | None = None
    best_score = 0
    best_origin = ""
    total_found = 0

    for origin, payload in _payloads(soup):
        for node in _product_shaped(payload):
            total_found += 1
            score = _score(node)
            if score > best_score:
                best, best_score, best_origin = node, score, origin

    if best is None:
        return

    # Recommendations rails and cart stubs are product-shaped too; the one being
    # sold is the most completely described.
    suffix = f" ({total_found} product-shaped objects found)" if total_found > 1 else ""
    source = f"{SOURCE} {best_origin}{suffix}"

    _map(best, bundle, source)


def _map(node: dict[str, Any], bundle: CandidateBundle, source: str) -> None:
    """Read one product-shaped chunk of JSON and add its fields to the bundle."""
    lookup = _normalised(node)

    bundle.add(NAME, _first_text(lookup, _NAME_KEYS), f"{source} name", Tier.C)
    bundle.add(
        DESCRIPTION, _first_text(lookup, _DESCRIPTION_KEYS), f"{source} description", Tier.C
    )
    bundle.add(BRAND, _brand(lookup), f"{source} brand", Tier.C)
    bundle.add(CURRENCY, _first_text(lookup, _CURRENCY_KEYS), f"{source} currency", Tier.C)
    bundle.add(COLORS, _first_list(lookup, _COLOR_KEYS), f"{source} color", Tier.C)
    bundle.add(IMAGE_URLS, _first_list(lookup, _IMAGE_KEYS), f"{source} image", Tier.C)

    price = _first_number(lookup, _PRICE_KEYS)
    compare = _first_number(lookup, _COMPARE_KEYS)
    bundle.add(PRICE, price, f"{source} price", Tier.C)

    # Payloads often mirror the current price into the list price when not on sale.
    if compare is not None and price is not None and compare > price:
        bundle.add(COMPARE_AT_PRICE, compare, f"{source} compare-at", Tier.C)


def _payloads(soup: BeautifulSoup) -> Iterator[tuple[str, Any]]:
    """Find and parse the JSON blobs on the page, with a label saying where each was.

    Looks in three places: JSON script tags, assignments in ordinary scripts, and
    data- attributes.
    """
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").strip().lower()
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue

        if script_type == "application/json":
            parsed = _loads(raw)
            if parsed is not None:
                yield "script[type=application/json]", parsed
            continue

        if script_type == "application/ld+json":
            continue  # jsonld.py owns this, at a stronger tier.

        # For `window.STATE = {...};` take the widest brace-delimited span, which
        # needs no knowledge of the variable name.
        parsed = _loads_span(raw)
        if parsed is not None:
            yield "inline script", parsed

    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        for name, value in element.attrs.items():
            if not isinstance(value, str) or len(value) < _MIN_ATTR_JSON_LENGTH:
                continue
            stripped = value.lstrip()
            if not stripped.startswith(("{", "[")):
                continue
            parsed = _loads(value)
            if parsed is not None:
                # Some pages park the product record in an attribute rather than a
                # script tag, and "any attribute holding JSON" is still a shape.
                yield f"attribute {name}", parsed


def _loads(raw: str) -> Any | None:
    """Parse JSON, returning None instead of raising if it is malformed."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _loads_span(raw: str) -> Any | None:
    """Pull the outermost {...} out of a script and parse it.

    Needed because the JSON is usually surrounded by JavaScript, as in
    `window.state = {...};`, which is not valid JSON on its own.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    return _loads(raw[start : end + 1])


def _normalise_key(key: str) -> str:
    """Lowercase a key and drop its underscores, so "list_price" matches "listPrice"."""
    return key.replace("_", "").replace("-", "").casefold()


def _normalised(node: dict[str, Any]) -> dict[str, Any]:
    """Re-key a dict so lookups ignore case and underscores.

    Lets one lookup find "listPrice", "list_price" and "listprice" alike.
    """
    out: dict[str, Any] = {}
    for key, value in node.items():
        if isinstance(key, str):
            out.setdefault(_normalise_key(key), value)
    return out


def _is_product_shaped(node: dict[str, Any]) -> bool:
    """Does this chunk of JSON look like a product? It needs a name plus a price or id.

    Two signals rather than one, because a menu entry has a name and a star rating has
    a number, but only a product record has both.
    """
    lookup = _normalised(node)

    if _first_text(lookup, _NAME_KEYS) is None:
        return False
    if _first_number(lookup, _PRICE_KEYS) is not None:
        return True
    return _first_text(lookup, _ID_KEYS) is not None


def _score(node: dict[str, Any]) -> int:
    """Rate how fully this JSON describes a product, so the best one can be chosen.

    A page may embed dozens of product-shaped chunks; the one being sold is the most
    complete.
    """
    lookup = _normalised(node)
    groups = (
        _NAME_KEYS,
        _PRICE_KEYS,
        _COMPARE_KEYS,
        _CURRENCY_KEYS,
        _ID_KEYS,
        _DESCRIPTION_KEYS,
        _BRAND_KEYS,
        _IMAGE_KEYS,
        _COLOR_KEYS,
    )
    return sum(1 for group in groups if any(key in lookup for key in group))


def _product_shaped(payload: Any) -> Iterator[dict[str, Any]]:
    """Search nested JSON for anything product-shaped, with depth and count limits.

    The limits matter: these blobs can be enormous, and an unbounded search would hang.
    """
    budget = [_MAX_NODES]
    yield from _walk(payload, 0, budget)


def _walk(node: Any, depth: int, budget: list[int]) -> Iterator[dict[str, Any]]:
    """Recurse through JSON, yielding product-shaped dicts until the budget runs out."""
    if depth > _MAX_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1

    if isinstance(node, dict):
        if _is_product_shaped(node):
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value, depth + 1, budget)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                yield from _walk(item, depth + 1, budget)


def _first_text(lookup: dict[str, Any], keys: frozenset[str]) -> str | None:
    """The first of these keys that holds usable text, or None."""
    for key in keys:
        value = lookup.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _first_number(lookup: dict[str, Any], keys: frozenset[str]) -> float | None:
    """The first of these keys that holds a number, or None."""
    for key in keys:
        value = lookup.get(key)
        # Nested money objects are common: {"amount": 20, "currency": "USD"}.
        if isinstance(value, dict):
            nested = _normalised(value)
            for inner in ("amount", "value", "price"):
                number = coerce_number(nested.get(inner))
                if number is not None:
                    return number
            continue
        number = coerce_number(value)
        if number is not None:
            return number
    return None


def _first_list(lookup: dict[str, Any], keys: frozenset[str]) -> list[str]:
    """The first of these keys that holds a list of strings, or an empty list."""
    for key in keys:
        values = coerce_text_list(lookup.get(key))
        if values:
            return values
    return []


def _brand(lookup: dict[str, Any]) -> str | None:
    """The brand name, whether stored as a string or nested in an object."""
    for key in _BRAND_KEYS:
        value = lookup.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _normalised(value)
            name = _first_text(nested, _NAME_KEYS)
            if name:
                return name
    return None
