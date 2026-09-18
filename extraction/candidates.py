"""Extracted values tagged with their source and a provenance tier.

Layers return every reading they find rather than one answer, because pages
publish the same field several times with different values and which one is right
depends on the field. Arbitration decides; this just records.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum
from typing import Any, Iterator

from pydantic import BaseModel


class Tier(str, Enum):
    """Provenance ranking; a lower letter is closer to what the site declared.

    Ranked per field, not globally: og:title is a good name source and
    og:site_name is a bad brand source, though both are OpenGraph.
    """

    # Site explicitly published this as machine-readable product data.
    A = "A"  # schema.org JSON-LD, schema.org microdata
    # Declared for machines, but coarse and marketing-shaped.
    B = "B"  # OpenGraph, Twitter cards, <meta>, <title>
    # Precise, but the semantics are inferred from key names.
    C = "C"  # embedded application state located by JSON shape
    # Definitely what a shopper sees, but needs interpretation.
    D = "D"  # rendered text, DOM heuristics
    # No source anchor at all.
    E = "E"  # model inference


# Fixed once and shared by every layer; add() rejects anything outside this set,
# so a typo cannot silently split one field into two half-populated buckets.
NAME = "name"
PRICE = "price.price"
CURRENCY = "price.currency"
COMPARE_AT_PRICE = "price.compare_at_price"
DESCRIPTION = "description"
KEY_FEATURES = "key_features"
IMAGE_URLS = "image_urls"
VIDEO_URL = "video_url"
BRAND = "brand"
COLORS = "colors"
VARIANTS = "variants"

ALL_FIELDS: frozenset[str] = frozenset(
    {
        NAME,
        PRICE,
        CURRENCY,
        COMPARE_AT_PRICE,
        DESCRIPTION,
        KEY_FEATURES,
        IMAGE_URLS,
        VIDEO_URL,
        BRAND,
        COLORS,
        VARIANTS,
    }
)

# Fields whose value is a collection rather than a single reading. A second
# image is additional information; a second price is a conflict to resolve.
LIST_FIELDS: frozenset[str] = frozenset({KEY_FEATURES, IMAGE_URLS, COLORS, VARIANTS})


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One reading of one field, with its provenance.

    A dataclass rather than a pydantic model: internal, never crosses the API
    boundary, and the brief asks that pydantic models stay in models.py.
    """

    field: str
    value: Any
    source: str
    tier: Tier

    def __post_init__(self) -> None:
        if self.field not in ALL_FIELDS:
            raise ValueError(
                f"{self.field!r} is not in the candidate field vocabulary: "
                f"{sorted(ALL_FIELDS)}"
            )


class CandidateBundle:
    """Everything every layer found, in the order it was found."""

    def __init__(self) -> None:
        self._candidates: list[Candidate] = []

    def add(self, field: str, value: Any, source: str, tier: Tier) -> Candidate | None:
        """Record a candidate, ignoring empty values.

        Filtering here means a layer can hand over whatever a standard gave it
        without first checking for absent, blank or empty.
        """
        cleaned = _clean(value)
        if cleaned is None:
            return None

        candidate = Candidate(field=field, value=cleaned, source=source, tier=tier)
        self._candidates.append(candidate)
        return candidate

    def __len__(self) -> int:
        return len(self._candidates)

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self._candidates)

    def fields(self) -> list[str]:
        """Fields that have at least one candidate, in first-seen order."""
        seen: dict[str, None] = {}
        for candidate in self._candidates:
            seen.setdefault(candidate.field, None)
        return list(seen)

    def for_field(self, field: str) -> list[Candidate]:
        """Candidates for one field, best tier first, insertion order within a tier."""
        indexed = [
            (candidate.tier.value, index, candidate)
            for index, candidate in enumerate(self._candidates)
            if candidate.field == field
        ]
        indexed.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in indexed]

    def best(self, field: str) -> Candidate | None:
        """Single strongest candidate, or None. Ties broken by first seen."""
        candidates = self.for_field(field)
        return candidates[0] if candidates else None

    def all_at_best_tier(self, field: str) -> list[Candidate]:
        """Every candidate sharing the strongest tier present for this field.

        Needed for list-valued fields, where each entry arrives as its own
        candidate: a variant matrix is many candidates, not one.
        """
        candidates = self.for_field(field)
        if not candidates:
            return []
        top = candidates[0].tier
        return [candidate for candidate in candidates if candidate.tier == top]

    def to_prompt_dict(
        self, max_chars: int = 1200, max_items: int = 40
    ) -> dict[str, list[dict[str, Any]]]:
        """Compact, JSON-safe view for the arbitration prompt.

        Identical values are collapsed with their sources merged. Truncation
        applies to this view only; the bundle keeps the full value for grounding.
        """
        out: dict[str, list[dict[str, Any]]] = {}
        for field in self.fields():
            merged: list[dict[str, Any]] = []
            by_value: dict[str, dict[str, Any]] = {}

            for candidate in self.for_field(field):
                jsonable = _jsonable(candidate.value)
                truncated_value, truncated = _truncate(jsonable, max_chars, max_items)
                key = repr(jsonable)

                if key in by_value:
                    # Same value from a weaker source: keep the strong tier, but
                    # record the corroboration.
                    by_value[key]["source"] += f"; {candidate.source}"
                    continue

                entry: dict[str, Any] = {
                    "value": truncated_value,
                    "source": candidate.source,
                    "tier": candidate.tier.value,
                }
                if truncated:
                    entry["truncated"] = True
                by_value[key] = entry
                merged.append(entry)

            out[field] = merged
        return out


def _clean(value: Any) -> Any | None:
    """Normalise a candidate value, returning None when it carries nothing."""
    if value is None:
        return None

    if isinstance(value, str):
        return collapse(value) or None

    if isinstance(value, (list, tuple)):
        items = [item for item in (_clean(entry) for entry in value) if item is not None]
        return items or None

    if isinstance(value, dict):
        return value or None

    if isinstance(value, bool):
        return value

    return value


def _jsonable(value: Any) -> Any:
    """Convert pydantic models and nested containers to plain JSON types."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _truncate(value: Any, max_chars: int, max_items: int) -> tuple[Any, bool]:
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars].rstrip() + "\u2026", True
    if isinstance(value, list) and len(value) > max_items:
        return value[:max_items], True
    return value, False


def collapse(value: str) -> str:
    """Collapse the whitespace runs that pretty-printed markup leaves in text."""
    return re.sub(r"\s+", " ", value).strip()


def local_name(value: str) -> str:
    """Last segment of a schema.org term, which may be written as a full URL."""
    return value.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip()


_NUMBER_PATTERN = re.compile(r"-?\d[\d.,\u00a0\u202f ]*\d|-?\d")


def coerce_number(value: Any) -> float | None:
    """Parse a price out of the formats publishers use.

    When both separators are present the rightmost is the decimal point, which
    holds in every grouping locale. A lone comma before exactly two digits is a
    decimal separator, so "29,99" is 29.99 and "1,299" is 1299.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    match = _NUMBER_PATTERN.search(value)
    if not match:
        return None

    raw = re.sub(r"[\s\u00a0\u202f]", "", match.group(0))
    has_dot, has_comma = "." in raw, "," in raw

    if has_dot and has_comma:
        decimal_sep = "." if raw.rindex(".") > raw.rindex(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        tail = raw.rsplit(",", 1)[1]
        raw = raw.replace(",", "." if len(tail) == 2 else "")
    elif has_dot and len(raw.rsplit(".", 1)[1]) == 3 and raw.count(".") > 1:
        # "1.234.567" -- grouping only, no decimal part.
        raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return None


def coerce_text_list(value: Any) -> list[str]:
    """Flatten a schema.org value that may be a scalar, a list, or nested."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        # An ImageObject/VideoObject stands in for its URL.
        for key in ("url", "contentUrl", "embedUrl", "name", "@id"):
            if isinstance(value.get(key), str):
                return [value[key]]
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(coerce_text_list(item))
        return out
    return []
