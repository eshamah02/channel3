"""Variant axes recovered from the page's own selection controls. Tier D.

On a page whose variant data is assembled client-side, the pickers are the only
surviving evidence of what can be selected. Groups are found through WAI-ARIA roles,
the select element and fieldsets of radio inputs, and named through the
accessible-name computation rather than class names or DOM position.

A picker reveals the axes and their values, not which combinations exist, so each
observed value becomes a single-axis Variant and no combination is invented here.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup, Tag

from extraction.candidates import VARIANTS, CandidateBundle, Tier
from extraction.layers.text import NUMBER, SYMBOL_CLASS
from models import Variant, VariantAttribute

logger = logging.getLogger(__name__)

SOURCE = "text"


def extract(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Add tier-D variant candidates from the page's selection controls."""
    _map_rendered_variants(soup, bundle)


# Generic e-commerce UI vocabulary for controls that are not variant axes.
_NON_AXIS_LABELS = frozenset(
    {
        "quantity",
        "qty",
        "country",
        "region",
        "currency",
        "language",
        "sort",
        "sortby",
        "page",
        "store",
        "zip",
        "zipcode",
        "postcode",
        "postalcode",
        "state",
        "email",
        "search",
        "shipping",
    }
)

# When these appear in the option values themselves, the accessible name is
# narrating the widget rather than naming a choice, so the group is not a picker.
_CONTROL_WORDS = frozenset(
    {"radio", "button", "checkbox", "dropdown", "toggle", "input", "field"}
)

# Words that describe the control rather than the axis.
_LABEL_NOISE = ("option", "options", "selection", "select", "choose", "pick", "item")

_PLACEHOLDER_VALUES = frozenset(
    {"", "-", "--", "select", "choose", "none", "please select", "select one", "n/a"}
)

_MAX_AXIS_VALUES = 60

# A trailing price inside an accessible name ("Color Option: Black, $29.95").
_TRAILING_PRICE_RE = re.compile(
    rf"[,;\u2013\u2014-]?\s*[{SYMBOL_CLASS}]\s?{NUMBER}\s*$"
)


def _map_rendered_variants(soup: BeautifulSoup, bundle: CandidateBundle) -> None:
    """Recover variant axes from the controls a shopper uses to choose.

    Groups are found through WAI-ARIA roles, the select element and fieldsets of
    radio inputs, and named through the accessible-name computation rather than
    class names or DOM position.

    A picker reveals the axes and their values, not which combinations exist, so
    each value becomes a single-axis Variant and no combination is invented.
    """
    seen_axes: dict[str, list[str]] = {}

    for label, values in _picker_groups(soup):
        if not _is_variant_axis(label, values):
            continue
        existing = seen_axes.setdefault(label, [])
        for value in values:
            if value not in existing:
                existing.append(value)

    for label, values in seen_axes.items():
        for value in values[:_MAX_AXIS_VALUES]:
            bundle.add(
                VARIANTS,
                Variant(option_values=[VariantAttribute(name=label, value=value)]),
                f"{SOURCE} rendered picker: {label} (axis value, combination unknown)",
                Tier.D,
            )


def _is_variant_axis(label: str, values: list[str]) -> bool:
    """Reject grouped controls that are not product variants.

    Quantity steppers, store locators, address forms and marketing surveys all look
    structurally identical to a picker, so each test keys off generic UI vocabulary
    or the shape of the values.
    """
    if label.rstrip().endswith("?"):
        # A question is a survey prompt; product axes are noun phrases.
        return False

    tokens = {token.strip(":,.").casefold() for token in re.split(r"\s+", label)}
    if tokens & _NON_AXIS_LABELS:
        return False

    if not values:
        return False

    # The accessible names describe the widget rather than the choice.
    narrated = sum(
        1
        for value in values
        if {word.casefold() for word in re.split(r"\s+", value)} & _CONTROL_WORDS
    )
    if narrated > len(values) // 2:
        return False

    # A run of integers from 1 is a quantity stepper whatever its label says.
    if len(values) > 1 and all(value.isdigit() for value in values):
        numbers = [int(value) for value in values]
        if numbers == list(range(1, len(numbers) + 1)):
            return False

    return True


def _is_offscreen(group: Tag) -> bool:
    """Modals and hidden panels are not the product's pickers.

    aria-hidden and the dialog role are how a page says "not currently part of the
    document".
    """
    for parent in [group, *group.parents]:
        if parent.get("aria-hidden") == "true":
            return True
        if parent.name == "dialog" or parent.get("role") in {"dialog", "alertdialog"}:
            return True
    return False


def _picker_groups(soup: BeautifulSoup) -> Iterator[tuple[str, list[str]]]:
    for group, options in _candidate_groups(soup):
        if _is_offscreen(group):
            continue
        result = _describe_group(group, options, soup)
        if result:
            yield result


def _candidate_groups(soup: BeautifulSoup) -> Iterator[tuple[Tag, list[Tag]]]:
    """Grouped selection controls, located through ARIA roles and HTML semantics."""
    # WAI-ARIA composite widgets.
    for role, option_role in (("radiogroup", "radio"), ("listbox", "option")):
        for group in soup.find_all(attrs={"role": role}):
            yield group, group.find_all(attrs={"role": option_role})

    # aria-pressed toggle buttons are the ARIA pattern for a picker built from
    # buttons; requiring an accessible name keeps arbitrary button rows out.
    for group in soup.find_all(attrs={"role": ["group", "toolbar"]}):
        toggles = [
            element
            for element in group.find_all(True)
            if element.has_attr("aria-pressed") or element.has_attr("aria-checked")
        ]
        if toggles:
            yield group, toggles

    # Native selects.
    for select in soup.find_all("select"):
        yield select, select.find_all("option")

    # Fieldsets of radio inputs.
    for fieldset in soup.find_all("fieldset"):
        yield fieldset, [
            element
            for element in fieldset.find_all("input")
            if (element.get("type") or "").lower() == "radio"
        ]


def _describe_group(
    group: Tag, options: list[Tag], soup: BeautifulSoup
) -> tuple[str, list[str]] | None:
    if len(options) < 1:
        return None

    values: list[str] = []
    descriptions: list[str] = []
    for option in options:
        value = _option_value(option)
        description = _option_description(option)
        if value is None:
            continue
        values.append(value)
        descriptions.append(description or value)

    values = [value for value in values if value.casefold() not in _PLACEHOLDER_VALUES]
    if not values:
        return None

    # The text the options share is the axis restated on every one of them.
    shared = _common_prefix(descriptions)

    label = _group_label(group, soup) or _axis_from_prefix(shared)
    if not label:
        return None

    cleaned = _clean_label(label)
    if not cleaned:
        return None

    # Strip the shared prefix rather than the cleaned axis name: "Colour Option:
    # Black" minus "Colour" would leave "Option: Black".
    final: list[str] = []
    for value, description in zip(values, descriptions):
        candidate = value
        if shared and description.casefold().startswith(shared.casefold()):
            remainder = description[len(shared) :].strip(" :,-")
            if remainder:
                candidate = remainder
        candidate = _strip_prefix(candidate, cleaned)
        candidate = _TRAILING_PRICE_RE.sub("", candidate).strip(" :,-\u2013")
        candidate = _strip_leading_noise(candidate)
        if candidate and candidate.casefold() not in _PLACEHOLDER_VALUES:
            final.append(candidate)

    deduped: list[str] = []
    for value in final:
        if value.casefold() not in {existing.casefold() for existing in deduped}:
            deduped.append(value)

    return (cleaned, deduped) if deduped else None


def _option_value(option: Tag) -> str | None:
    """The shortest accessible rendering of the choice itself."""
    for getter in (
        lambda: option.get("title"),
        lambda: option.get("aria-label"),
        lambda: option.get_text(" ", strip=True),
        lambda: option.get("value"),
        lambda: (option.find("img") or {}).get("alt") if option.find("img") else None,
    ):
        value = getter()
        if isinstance(value, str) and value.strip():
            return _TRAILING_PRICE_RE.sub("", value.strip()).strip(" :,-") or None
    return None


def _option_description(option: Tag) -> str | None:
    """The most descriptive accessible name, which often carries the axis too."""
    for getter in (
        lambda: option.get("aria-label"),
        lambda: (option.find("img") or {}).get("alt") if option.find("img") else None,
        lambda: option.get("title"),
        lambda: option.get_text(" ", strip=True),
    ):
        value = getter()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _group_label(group: Tag, soup: BeautifulSoup) -> str | None:
    """Accessible name of the group, per the ARIA/HTML labelling mechanisms."""
    aria_label = group.get("aria-label")
    if isinstance(aria_label, str) and aria_label.strip():
        return aria_label.strip()

    labelled_by = group.get("aria-labelledby")
    if isinstance(labelled_by, str):
        for token in labelled_by.split():
            target = soup.find(id=token)
            if target is not None:
                text = target.get_text(" ", strip=True)
                if text:
                    return text

    if group.name == "fieldset":
        legend = group.find("legend")
        if legend is not None:
            text = legend.get_text(" ", strip=True)
            if text:
                return text

    group_id = group.get("id")
    if isinstance(group_id, str) and group_id:
        label = soup.find("label", attrs={"for": group_id})
        if label is not None:
            text = label.get_text(" ", strip=True)
            if text:
                return text

    name = group.get("name")
    if isinstance(name, str) and name.strip():
        return re.sub(r"[_\-]+", " ", name).strip()

    return None


def _common_prefix(descriptions: list[str]) -> str:
    """The leading text every option repeats, up to the last separator.

    Accessible names on a picker are conventionally "<axis>: <value>", so what the
    options share is the axis. A convention in label text, not a standard.
    """
    if not descriptions:
        return ""

    if len(descriptions) == 1:
        head, separator, tail = descriptions[0].partition(":")
        return head + separator if (separator and tail.strip()) else ""

    prefix = descriptions[0]
    for description in descriptions[1:]:
        limit = min(len(prefix), len(description))
        index = 0
        while index < limit and prefix[index].casefold() == description[index].casefold():
            index += 1
        prefix = prefix[:index]
        if not prefix.strip():
            return ""

    # Cut at the last separator so a shared word from the values themselves
    # ("Small"/"Smaller") cannot leak into the axis.
    for separator in (":", "\u2013", "-"):
        if separator in prefix:
            return prefix[: prefix.rindex(separator) + 1]

    # Without a separator the overlap may be accidental: ["30", "32", "34"] share a
    # leading "3" and mean nothing by it.
    if prefix != prefix.rstrip() and len(prefix.strip()) >= 3:
        return prefix
    return ""


def _axis_from_prefix(prefix: str) -> str | None:
    cleaned = prefix.strip().strip(":,-\u2013 ").strip()
    return cleaned or None


def _strip_prefix(value: str, prefix: str) -> str:
    if prefix and value.casefold().startswith(prefix.casefold()) and len(value) > len(prefix):
        return value[len(prefix) :].strip(" :,-\u2013")
    return value


def _strip_leading_noise(value: str) -> str:
    """Remove control vocabulary left at the front of a value."""
    result = value
    for _ in range(3):
        words = re.split(r"\s+", result, maxsplit=1)
        if not words or words[0].strip(":,").casefold() not in _LABEL_NOISE:
            break
        result = (words[1] if len(words) > 1 else "").strip(" :,-\u2013")
    return result


def _clean_label(label: str) -> str | None:
    """Reduce an accessible label to an axis name."""
    cleaned = label.strip().strip(":,-\u2013 ").strip()
    words = [word for word in re.split(r"\s+", cleaned) if word]
    kept = [word for word in words if word.casefold().strip(":,") not in _LABEL_NOISE]
    if not kept:
        # The label was entirely control vocabulary, e.g. "Select an option".
        return None
    result = " ".join(kept).strip(":,-\u2013 ").strip()
    if not result or len(result) > 40:
        return None
    return result[:1].upper() + result[1:]
