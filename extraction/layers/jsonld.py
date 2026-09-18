"""schema.org JSON-LD, per https://www.w3.org/TR/json-ld11/. Tier A.

The site declared this for machines, so it outranks everything else. Most of the
work is accepting the shapes the spec permits, since publishers use all of them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from bs4 import BeautifulSoup

from extraction.candidates import (
    BRAND,
    COLORS,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    NAME,
    PRICE,
    VARIANTS,
    VIDEO_URL,
    CandidateBundle,
    Tier,
    coerce_number,
    coerce_text_list,
    local_name,
)
from models import Price, Variant, VariantAttribute

logger = logging.getLogger(__name__)

SOURCE = "JSON-LD"

# schema.org types that describe the thing being sold.
_PRODUCT_TYPES = frozenset({"product", "productgroup", "individualproduct"})

# Properties that carry a WebPage's primary entity, which is where a Product
# node commonly sits when the publisher describes the page rather than the item.
_ENTRY_PROPERTIES = ("mainentity", "mainentityofpage")

# schema.org properties that act as variant axes, named by the vocabulary rather
# than by any site's markup. ProductGroup.variesBy wins when present.
_AXIS_PROPERTIES = {
    "size": "Size",
    "color": "Color",
    "colour": "Colour",
    "material": "Material",
    "pattern": "Pattern",
}

_IN_STOCK = frozenset(
    {"instock", "onlineonly", "instoreonly", "limitedavailability", "presale"}
)
_OUT_OF_STOCK = frozenset({"outofstock", "soldout", "discontinued"})


def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Read the page's JSON-LD and add what it finds to the bundle as tier A.

    JSON-LD is a block of JSON in a script tag where the site states outright that the
    page is a product and what it costs, so it is the most trustworthy source there is.
    """
    for index, node in enumerate(_product_nodes(soup)):
        # Several product nodes is unusual but legal; the suffix keeps the
        # provenance readable when it happens.
        suffix = "" if index == 0 else f" #{index + 1}"
        _map_product(node, bundle, suffix)


def _product_nodes(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Find every block of JSON-LD on the page that describes a product."""
    nodes: list[dict[str, Any]] = []
    for payload in _payloads(soup):
        nodes.extend(_find_products(payload))
    return nodes


def _payloads(soup: BeautifulSoup) -> Iterator[Any]:
    """Parse each JSON-LD script tag, skipping any that contain broken JSON.

    Handled one at a time because malformed JSON-LD is common and one bad block must
    not cost us the good ones.
    """
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").strip().lower()
        if script_type != "application/ld+json":
            continue

        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue

        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Skipping malformed JSON-LD block: %s", exc)


def _find_products(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Search nested JSON-LD for product entries.

    Only follows a fixed list of safe keys. Following something like "isRelatedTo"
    would pull in a *different* product's price and report it as this one's.
    """
    if depth > 6:
        return []

    if isinstance(payload, list):
        found: list[dict[str, Any]] = []
        for item in payload:
            found.extend(_find_products(item, depth + 1))
        return found

    if not isinstance(payload, dict):
        return []

    if _is_product(payload):
        return [payload]

    found = []
    graph = payload.get("@graph")
    if graph is not None:
        found.extend(_find_products(graph, depth + 1))

    for key, value in payload.items():
        if key.lower() in _ENTRY_PROPERTIES:
            found.extend(_find_products(value, depth + 1))

    return found


def _types(node: dict[str, Any]) -> set[str]:
    """The schema.org types on a node, as lowercase names.

    Handles @type being a single word, a list, or a full URL, all of which are legal.
    """
    raw = node.get("@type") or node.get("type") or []
    values = raw if isinstance(raw, list) else [raw]
    return {
        local_name(str(value)).lower()
        for value in values
        if value
    }


def _is_product(node: dict[str, Any]) -> bool:
    """True if this JSON-LD node describes a product."""
    return bool(_types(node) & _PRODUCT_TYPES)


def _map_product(node: dict[str, Any], bundle: CandidateBundle, suffix: str) -> None:
    """Read one product node and add each field it publishes to the bundle."""
    source = f"{SOURCE}{suffix}"

    bundle.add(NAME, node.get("name"), f"{source} name", Tier.A)
    bundle.add(DESCRIPTION, node.get("description"), f"{source} description", Tier.A)
    bundle.add(BRAND, _brand_name(node.get("brand")), f"{source} brand", Tier.A)
    bundle.add(COLORS, coerce_text_list(node.get("color")), f"{source} color", Tier.A)
    bundle.add(IMAGE_URLS, coerce_text_list(node.get("image")), f"{source} image", Tier.A)

    for key in ("video", "subjectOf"):
        urls = _video_urls(node.get(key))
        if urls:
            bundle.add(VIDEO_URL, urls[0], f"{source} {key}", Tier.A)
            break

    priced = _map_offers(node.get("offers"), bundle, source)
    variants = _map_variants(node, bundle, source)

    # A ProductGroup may be unpriced and price each SKU instead, in which case
    # the page yields no price at all without this.
    if not priced:
        _price_from_variants(variants, bundle, source)


def _brand_name(brand: Any) -> str | None:
    """The brand name, whether it was published as an object or just a string."""
    if isinstance(brand, str):
        return brand
    if isinstance(brand, dict):
        name = brand.get("name")
        return name if isinstance(name, str) else None
    if isinstance(brand, (list, tuple)):
        for item in brand:
            name = _brand_name(item)
            if name:
                return name
    return None


def _video_urls(value: Any) -> list[str]:
    """Video URLs, from a video object's contentUrl/embedUrl or a plain string."""
    if isinstance(value, dict):
        for key in ("contentUrl", "embedUrl", "url"):
            if isinstance(value.get(key), str):
                return [value[key]]
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_video_urls(item))
        return out
    return []


def _offer_nodes(offers: Any) -> Iterator[dict[str, Any]]:
    """All the offers on a product, flattening lists and offers nested inside offers.

    An "offer" is schema.org's term for the pricing: amount, currency, availability.
    """
    if isinstance(offers, dict):
        yield offers
        # AggregateOffer may enumerate its constituent Offers.
        nested = offers.get("offers")
        if nested is not None:
            yield from _offer_nodes(nested)
    elif isinstance(offers, (list, tuple)):
        for item in offers:
            yield from _offer_nodes(item)


def _map_offers(offers: Any, bundle: CandidateBundle, source: str) -> bool:
    """Record price, currency and availability from the offers. True if a price existed."""
    found = False
    for offer in _offer_nodes(offers):
        price = coerce_number(offer.get("price"))
        low = coerce_number(offer.get("lowPrice"))
        high = coerce_number(offer.get("highPrice"))

        # priceSpecification is the long-form alternative to a bare price.
        spec = offer.get("priceSpecification")
        if price is None and isinstance(spec, dict):
            price = coerce_number(spec.get("price"))

        current = price if price is not None else low
        if current is not None:
            bundle.add(PRICE, current, f"{source} offers.price", Tier.A)
            found = True

        # A highPrice above the asking price is the former price.
        if high is not None and current is not None and high > current:
            bundle.add(
                COMPARE_AT_PRICE, high, f"{source} offers.highPrice", Tier.A
            )

        currency = offer.get("priceCurrency")
        if not currency and isinstance(spec, dict):
            currency = spec.get("priceCurrency")
        bundle.add(CURRENCY, currency, f"{source} offers.priceCurrency", Tier.A)

    return found


def _price_from_variants(
    variants: list[Variant], bundle: CandidateBundle, source: str
) -> None:
    """Take a price from the variants when the product itself doesn't state one.

    Uses the cheapest, which matches the "from $X" wording shops normally show.
    """
    priced = [variant.price for variant in variants if variant.price is not None]
    if not priced:
        return

    cheapest = min(priced, key=lambda price: price.price)
    bundle.add(PRICE, cheapest.price, f"{source} hasVariant offers.price", Tier.A)
    bundle.add(
        CURRENCY, cheapest.currency, f"{source} hasVariant offers.priceCurrency", Tier.A
    )

    highest = max(price.price for price in priced)
    if highest > cheapest.price:
        bundle.add(
            COMPARE_AT_PRICE,
            highest,
            f"{source} hasVariant offers.price (highest of {len(priced)})",
            Tier.A,
        )


def _map_variants(
    node: dict[str, Any], bundle: CandidateBundle, source: str
) -> list[Variant]:
    """Turn the published variant list into Variant candidates.

    Frequently the only full account of which combinations exist, because the on-page
    pickers can be built entirely by JavaScript and leave no trace in the HTML.
    """
    entries = node.get("hasVariant")
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, (list, tuple)):
        return []

    axes = _declared_axes(node)
    built: list[Variant] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Entries with no option values are placeholders, often a bare link to
        # another colourway, so they are skipped rather than emitted as empty.
        variant = _build_variant(entry, axes)
        if variant is not None:
            built.append(variant)
            bundle.add(VARIANTS, variant, f"{source} hasVariant", Tier.A)
    return built


def _declared_axes(node: dict[str, Any]) -> list[str]:
    """The axis names the product says it varies by, such as size and colour."""
    raw = node.get("variesBy")
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    axes = []
    for value in values:
        if not isinstance(value, str):
            continue
        prop = local_name(value)
        if prop:
            axes.append(prop)
    return axes


def _build_variant(entry: dict[str, Any], declared_axes: list[str]) -> Variant | None:
    """Turn one published variant into a Variant, or None if it names no options."""
    attributes: list[VariantAttribute] = []
    seen_axes: set[str] = set()

    def add_axis(label: str, value: Any) -> None:
        """Add one option to this variant, e.g. Size=Large."""
        text = coerce_text_list(value)
        if not text or label.casefold() in seen_axes:
            return
        seen_axes.add(label.casefold())
        attributes.append(VariantAttribute(name=label, value=text[0]))

    # Declared axes first, so ordering follows the publisher's own statement.
    for prop in declared_axes:
        add_axis(_AXIS_PROPERTIES.get(prop.casefold(), prop.title()), entry.get(prop))

    for prop, label in _AXIS_PROPERTIES.items():
        add_axis(label, entry.get(prop))

    # additionalProperty carries axes the core vocabulary has no term for.
    extra = entry.get("additionalProperty")
    if isinstance(extra, dict):
        extra = [extra]
    if isinstance(extra, (list, tuple)):
        for prop in extra:
            if isinstance(prop, dict) and isinstance(prop.get("name"), str):
                add_axis(prop["name"], prop.get("value"))

    if not attributes:
        return None

    price = None
    in_stock = None
    for offer in _offer_nodes(entry.get("offers")):
        amount = coerce_number(offer.get("price"))
        currency = offer.get("priceCurrency")
        if price is None and amount is not None and isinstance(currency, str):
            price = Price(price=amount, currency=currency)
        if in_stock is None:
            in_stock = _availability(offer.get("availability"))

    sku = entry.get("sku")
    return Variant(
        option_values=attributes,
        sku=sku if isinstance(sku, str) else None,
        price=price,
        in_stock=in_stock,
        image_urls=coerce_text_list(entry.get("image")),
    )


def _availability(value: Any) -> bool | None:
    """Is this in stock? True, False, or None when the page doesn't say."""
    if not isinstance(value, str):
        return None
    token = local_name(value).casefold()
    if token in _IN_STOCK:
        return True
    if token in _OUT_OF_STOCK:
        return False
    return None
