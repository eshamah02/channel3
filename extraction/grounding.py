"""Assertions that an extracted value really appears in the source.

Failures append to ExtractionMetadata.warnings rather than failing the page, where
the escalation policy reads them.

Only values distinctive enough to give a meaningful answer are checked. Prices are
deliberately excluded: the bare numeric string for a price occurs 175 times in one
of these sources, so a substring test passes by accident and carries no information.
A price is protected by its provenance tier instead.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

from extraction.candidates import collapse

logger = logging.getLogger(__name__)

# A segment shorter than this is not distinctive enough for its presence in the
# source to mean anything.
_MIN_SEGMENT_LENGTH = 6


def verify_urls(urls: list[str], raw_html: str) -> list[str]:
    """Warn about URLs with no anchor in the source.

    Matched on the longest path segment rather than the whole URL, since
    normalisation deliberately rewrote these.
    """
    warnings: list[str] = []
    decoded = unquote(raw_html)

    for url in urls:
        segment = _longest_segment(url)
        if segment is None:
            warnings.append(f"image URL has no verifiable path segment: {url}")
            continue
        if segment in raw_html or segment in decoded or unquote(segment) in decoded:
            continue
        warnings.append(f"image URL not found in source: {url}")

    return warnings


def _longest_segment(url: str) -> str | None:
    path = urlparse(url).path
    segments = [segment for segment in path.split("/") if len(segment) >= _MIN_SEGMENT_LENGTH]
    return max(segments, key=len) if segments else None


def verify_text(value: str, raw_html: str, *, label: str) -> list[str]:
    """Warn when a short text value has no anchor in the source.

    Only for values the model should have selected rather than written; checking
    synthesised prose would flag correct behaviour.
    """
    cleaned = collapse(value)
    if not cleaned or len(cleaned) < 3:
        return []
    if cleaned.casefold() in raw_html.casefold():
        return []
    return [f"{label} {cleaned!r} not found in source"]
