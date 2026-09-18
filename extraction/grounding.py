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
    """Check each image URL really came from the page. Returns a warning per failure.

    Compares the longest part of the URL path rather than the whole URL, because
    normalisation deliberately rewrote these to ask for full-resolution versions.
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
    """The longest chunk between slashes in a URL path, if any is long enough.

    Short chunks are skipped because finding "is" in a page proves nothing.
    """
    path = urlparse(url).path
    segments = [segment for segment in path.split("/") if len(segment) >= _MIN_SEGMENT_LENGTH]
    return max(segments, key=len) if segments else None


def verify_text(value: str, raw_html: str, *, label: str) -> list[str]:
    """Check a short text value appears somewhere in the page. Returns any warning.

    Only for values the model was supposed to pick, like a name or brand. Running it
    on the description would flag the model for doing its job, since we asked it to
    write that from scratch.
    """
    cleaned = collapse(value)
    if not cleaned or len(cleaned) < 3:
        return []
    if cleaned.casefold() in raw_html.casefold():
        return []
    return [f"{label} {cleaned!r} not found in source"]
