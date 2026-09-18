"""OpenGraph (https://ogp.me/), the product:* extension, Twitter cards, <title>.

Tier B rather than A: declared for machines, but marketing surface. og:title is
routinely "Name | Free Shipping | Store", which contains the name without being it.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from extraction.candidates import (
    BRAND,
    COMPARE_AT_PRICE,
    CURRENCY,
    DESCRIPTION,
    IMAGE_URLS,
    NAME,
    PRICE,
    VIDEO_URL,
    CandidateBundle,
    Tier,
    coerce_number,
)

SOURCE = "meta"

# Both property= and name= are read for every key, because publishers mix up which
# one they use often enough that insisting on the correct one loses real data.
_TAGS: dict[str, str] = {
    "og:title": NAME,
    "twitter:title": NAME,
    "og:description": DESCRIPTION,
    "twitter:description": DESCRIPTION,
    "description": DESCRIPTION,
    "og:image": IMAGE_URLS,
    "og:image:url": IMAGE_URLS,
    "og:image:secure_url": IMAGE_URLS,
    "twitter:image": IMAGE_URLS,
    "twitter:image:src": IMAGE_URLS,
    "og:video": VIDEO_URL,
    "og:video:url": VIDEO_URL,
    "og:video:secure_url": VIDEO_URL,
    "twitter:player": VIDEO_URL,
    "og:price:amount": PRICE,
    "product:price:amount": PRICE,
    "og:price:currency": CURRENCY,
    "product:price:currency": CURRENCY,
    "product:original_price:amount": COMPARE_AT_PRICE,
    "og:brand": BRAND,
    "product:brand": BRAND,
}

_NUMERIC_FIELDS = frozenset({PRICE, COMPARE_AT_PRICE})

# Structurally tier B, but as a brand signal it is the shop rather than the
# manufacturer, so it is demoted to keep it available while distrusted.
_BRAND_TRAP = "og:site_name"


def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Read the <head> tags and add what they say as tier B.

    These are the OpenGraph and Twitter tags that make link previews on social media,
    so they hold the right facts wrapped in marketing: og:title is regularly
    "Name | Free Shipping | Store", which contains the name without being it.
    """
    for element in soup.find_all("meta"):
        key = element.get("property") or element.get("name") or element.get("itemprop")
        if isinstance(key, list):
            key = " ".join(key)
        if not isinstance(key, str):
            continue
        key = key.strip().casefold()

        content = element.get("content")
        if isinstance(content, list):
            content = " ".join(content)
        if not isinstance(content, str) or not content.strip():
            continue

        if key == _BRAND_TRAP:
            bundle.add(BRAND, content, f"{SOURCE} {_BRAND_TRAP}", Tier.D)
            continue

        field = _TAGS.get(key)
        if field is None:
            continue

        value = coerce_number(content) if field in _NUMERIC_FIELDS else content
        bundle.add(field, value, f"{SOURCE} {key}", Tier.B)

    title = soup.find("title")
    if title is not None:
        bundle.add(NAME, title.get_text(" ", strip=True), f"{SOURCE} <title>", Tier.B)
