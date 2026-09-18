"""Rendered text and accessible control names. Tier D.

Last resort, and the only source on a page that declares nothing. A number next to
a currency symbol might be the price, a delivery threshold or a saving, so
candidates carry enough context for arbitration to choose rather than this layer
pretending to know.

Nothing here mutates the shared tree. Removing script elements to get the visible
text would delete the only copy of the price on pages that publish it in JSON-LD.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup, NavigableString, Tag

from extraction.candidates import (
    COMPARE_AT_PRICE,
    CURRENCY,
    KEY_FEATURES,
    NAME,
    PRICE,
    VARIANTS,
    CandidateBundle,
    Tier,
    coerce_number,
)
from models import Variant, VariantAttribute

logger = logging.getLogger(__name__)

SOURCE = "text"

# Elements whose text is never shown to a shopper.
_NON_VISIBLE = frozenset({"script", "style", "noscript", "template", "head", "title"})

# ISO 4217 codes common enough on storefronts to be worth recognising in prose.
_ISO_CODES = (
    "USD EUR GBP JPY CAD AUD CHF CNY SEK NOK DKK PLN CZK HUF RON BGN TRY RUB INR "
    "BRL MXN ARS CLP COP PEN ZAR NGN KES AED SAR ILS EGP HKD SGD TWD KRW THB MYR "
    "IDR PHP VND NZD ISK UAH"
).split()

# Unambiguous currency symbols.
_SYMBOL_TO_ISO = {
    "\u00a3": "GBP",
    "\u20ac": "EUR",
    "\u00a5": "JPY",
    "\u20b9": "INR",
    "\u20bd": "RUB",
    "\u20a9": "KRW",
    "\u20aa": "ILS",
    "\u20b4": "UAH",
    "\u0e3f": "THB",
    "\u20ab": "VND",
    "\u20b1": "PHP",
}

# "$" is shared by a dozen currencies, so the region in html[lang] decides.
_REGION_TO_DOLLAR = {
    "US": "USD",
    "CA": "CAD",
    "AU": "AUD",
    "NZ": "NZD",
    "SG": "SGD",
    "HK": "HKD",
    "MX": "MXN",
    "AR": "ARS",
    "CL": "CLP",
    "CO": "COP",
    "TW": "TWD",
    "BR": "BRL",
}
_DOLLAR_DEFAULT = "USD"

_iso_alternation = "|".join(_ISO_CODES)
SYMBOL_CLASS = "".join(re.escape(symbol) for symbol in _SYMBOL_TO_ISO) + re.escape("$")
# The non-capturing group is load-bearing: interpolated into a larger pattern, a
# bare alternation would split the whole enclosing pattern rather than the number.
NUMBER = r"(?:\d[\d.,\u00a0\u202f]*\d|\d)"

# Both orders, since publishers write "$29.99" and "29,99 EUR". Whitespace is
# allowed between token and number because they often sit in separate elements.
_PRICE_RE = re.compile(
    rf"(?P<cur_before>[{SYMBOL_CLASS}]|(?<![A-Za-z])(?:{_iso_alternation})(?![A-Za-z]))"
    rf"\s{{0,3}}(?P<num_after>{NUMBER})"
    rf"|(?P<num_before>{NUMBER})\s{{0,3}}"
    rf"(?P<cur_after>[{SYMBOL_CLASS}]|(?<![A-Za-z])(?:{_iso_alternation})(?![A-Za-z]))"
)

# Two adjacent prices within these bounds are the was/now pattern; wider ratios are
# usually a saving, a delivery threshold or an unrelated product.
_SALE_MAX_DISTANCE = 60
_SALE_MIN_RATIO = 1.05
_SALE_MAX_RATIO = 3.0

# Implausible as a retail price; filters out years, ids and quantities.
_MIN_PRICE = 0.01
_MAX_PRICE = 1_000_000.0

_MAX_PRICE_CANDIDATES = 4

def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Add tier-D candidates from rendered text."""
    text = _visible_text(soup)

    _map_prices(text, soup, bundle)
    _map_name(soup, bundle)
    _map_key_features(soup, bundle)


def _visible_text(soup: BeautifulSoup) -> str:
    """Concatenate the strings a shopper can read, without touching the tree."""
    parts: list[str] = []
    for string in soup.strings:
        if _hidden(string):
            continue
        stripped = str(string).strip()
        if stripped:
            parts.append(stripped)
    return "\n".join(parts)


def _hidden(string: NavigableString) -> bool:
    for parent in string.parents:
        if parent.name in _NON_VISIBLE:
            return True
    return False


# --- price -----------------------------------------------------------------


class _Reading:
    __slots__ = ("amount", "iso", "position")

    def __init__(self, amount: float, iso: str | None, position: int) -> None:
        self.amount = amount
        self.iso = iso
        self.position = position


def _scan_prices(text: str) -> list[_Reading]:
    readings: list[_Reading] = []
    for match in _PRICE_RE.finditer(text):
        token = match.group("cur_before") or match.group("cur_after")
        raw = match.group("num_after") or match.group("num_before")
        amount = coerce_number(raw)
        if amount is None or not _MIN_PRICE <= amount <= _MAX_PRICE:
            continue
        readings.append(
            _Reading(amount=amount, iso=_token_to_iso(token), position=match.start())
        )
    return readings


def _token_to_iso(token: str | None) -> str | None:
    if not token:
        return None
    if token.upper() in _ISO_CODES:
        return token.upper()
    return _SYMBOL_TO_ISO.get(token)


def _map_prices(text: str, soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    readings = _scan_prices(text)
    if not readings:
        return

    # Repetition is the most useful signal available without guessing: the real
    # price recurs, while a delivery threshold or a saving appears once.
    counts: dict[float, int] = {}
    for reading in readings:
        counts[reading.amount] = counts.get(reading.amount, 0) + 1

    emitted: set[float] = set()
    for rank, reading in enumerate(readings):
        if reading.amount in emitted:
            continue
        if len(emitted) >= _MAX_PRICE_CANDIDATES:
            break
        emitted.add(reading.amount)
        bundle.add(
            PRICE,
            reading.amount,
            f"{SOURCE} visible (occurrence {rank + 1}, seen {counts[reading.amount]}x)",
            Tier.D,
        )

    _map_sale_pair(readings, bundle)
    _map_currency(readings, soup, bundle)


def _map_sale_pair(readings: list[_Reading], bundle: CandidateBundle) -> None:
    """Two adjacent prices in the was/now pattern."""
    for first, second in zip(readings, readings[1:]):
        if second.position - first.position > _SALE_MAX_DISTANCE:
            continue
        low, high = sorted((first.amount, second.amount))
        if low <= 0 or not _SALE_MIN_RATIO <= high / low <= _SALE_MAX_RATIO:
            continue
        bundle.add(PRICE, low, f"{SOURCE} sale pair (lower of two adjacent)", Tier.D)
        bundle.add(
            COMPARE_AT_PRICE, high, f"{SOURCE} sale pair (higher of two adjacent)", Tier.D
        )
        return


def _map_currency(
    readings: list[_Reading], soup: BeautifulSoup, bundle: CandidateBundle
) -> None:
    """Resolve currency, and be explicit when the answer was inferred.

    The word "inferred" in the source string is a contract: the escalation policy
    greps for it to treat a guessed currency as weak extraction.
    """
    for reading in readings:
        if reading.iso:
            bundle.add(
                CURRENCY, reading.iso, f"{SOURCE} currency symbol or ISO code", Tier.D
            )
            return

    # Every reading used an ambiguous symbol, so the language region is all that
    # remains.
    region = _document_region(soup)
    if region and region in _REGION_TO_DOLLAR:
        bundle.add(
            CURRENCY,
            _REGION_TO_DOLLAR[region],
            f"{SOURCE} ambiguous symbol resolved by html lang region {region}",
            Tier.D,
        )
        return

    bundle.add(
        CURRENCY,
        _DOLLAR_DEFAULT,
        f"{SOURCE} ambiguous symbol, no region: currency inferred by default",
        Tier.D,
    )


def _document_region(soup: BeautifulSoup) -> str | None:
    """Region subtag from html[lang], else from an hreflang alternate."""
    html = soup.find("html")
    if html is not None:
        lang = html.get("lang")
        if isinstance(lang, str) and "-" in lang:
            return lang.split("-")[-1].upper()

    for link in soup.find_all("link", hreflang=True):
        hreflang = link.get("hreflang")
        if isinstance(hreflang, str) and "-" in hreflang and hreflang.lower() != "x-default":
            return hreflang.split("-")[-1].upper()
    return None


# --- name and features -----------------------------------------------------


def _map_name(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """The first h1."""
    heading = soup.find("h1")
    if heading is not None:
        bundle.add(NAME, heading.get_text(" ", strip=True), f"{SOURCE} first <h1>", Tier.D)


def _map_key_features(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Bullet lists in the main content, the only structured source for this field.

    A list qualifies when it has several short items that are not predominantly
    links, which excludes nav menus and breadcrumbs without naming a class.
    """
    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("body") or soup

    emitted = 0
    for list_element in root.find_all(["ul", "ol"]):
        if emitted >= 3:
            break
        if _is_chrome(list_element):
            continue
        items = _feature_items(list_element)
        if len(items) < 3:
            continue
        bundle.add(KEY_FEATURES, items[:15], f"{SOURCE} <{list_element.name}> in main content", Tier.D)
        emitted += 1


def _is_chrome(element: Tag) -> bool:
    for parent in element.parents:
        if parent.name in {"nav", "header", "footer", "aside", "form"}:
            return True
        if parent.get("role") in {"navigation", "banner", "contentinfo", "search"}:
            return True
    return False


def _feature_items(list_element: Tag) -> list[str]:
    items: list[str] = []
    linky = 0
    for item in list_element.find_all("li", recursive=False):
        text = item.get_text(" ", strip=True)
        if not 3 <= len(text) <= 300:
            continue
        link = item.find("a")
        if link is not None and link.get_text(" ", strip=True) == text:
            # Nothing but a link, which is what navigation looks like.
            linky += 1
        items.append(text)

    if not items or linky > len(items) // 2:
        return []
    return items
