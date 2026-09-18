"""Batch ingest: HTML files in, one JSON envelope per product out.

Drop a file in, re-run, and only the new file costs tokens; anything already
extracted is recognised by the hash of its source. The API serves these files
directly, so each has to be complete or absent, never half-written.
"""

from __future__ import annotations

import asyncio
import dataclasses
import glob as globlib
import json
import logging
import os
from pathlib import Path
from typing import Callable, Iterable

from pydantic import ValidationError

from extraction import llm
from extraction.derive import content_hash
from extraction.llm import LLMClient
from extraction.pipeline import DEFAULT_RESOLVER, extract
from models import ExtractedProduct

logger = logging.getLogger(__name__)

DEFAULT_INPUT_GLOB = "data/*.html"
DEFAULT_OUT_DIR = Path("out/products")
DEFAULT_CONCURRENCY = 5

ClientFactory = Callable[[], LLMClient]


@dataclasses.dataclass
class IngestResult:
    """A record of what one run did: what worked, what was skipped, what failed."""

    extracted: list[ExtractedProduct] = dataclasses.field(default_factory=list)
    skipped: list[str] = dataclasses.field(default_factory=list)
    failed: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    # Two inputs in one run that resolved to the same product id: (id, kept, dropped)
    collisions: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)

    @property
    def product_cost(self) -> float:
        """Mean cost of the products extracted in this run."""
        if not self.extracted:
            return 0.0
        return sum(item.extraction.cost_usd for item in self.extracted) / len(
            self.extracted
        )


def expand(paths: Iterable[str] | None) -> list[str]:
    """Turn command-line arguments into a list of files, defaulting to data/*.html.

    Patterns like "*.html" are expanded here rather than by the shell, so a quoted one
    still works. Duplicates are removed and order is kept.
    """
    patterns = list(paths or [])
    if not patterns:
        patterns = [DEFAULT_INPUT_GLOB]

    found: list[str] = []
    for pattern in patterns:
        if any(character in pattern for character in "*?["):
            found.extend(sorted(globlib.glob(pattern)))
        else:
            found.append(pattern)

    # Preserve order, drop repeats.
    seen: set[str] = set()
    unique: list[str] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def load_index(out_dir: Path) -> dict[str, str]:
    """Fingerprints of everything already extracted, so unchanged files can be skipped.

    Read once per run. A corrupt file is ignored rather than fatal, because
    re-extracting one page is recoverable and refusing to start is not.
    """
    index: dict[str, str] = {}
    if not out_dir.is_dir():
        return index

    for path in sorted(out_dir.glob("*.json")):
        try:
            envelope = ExtractedProduct.model_validate_json(path.read_text())
        except (ValidationError, ValueError, OSError) as exc:
            logger.warning("ignoring unreadable envelope %s: %s", path, exc)
            continue
        index[envelope.content_hash] = envelope.id
    return index


def write_envelope(envelope: ExtractedProduct, out_dir: Path) -> Path:
    """Save one product as JSON, all at once so it can never be half-written.

    Writes to a temporary name and then renames it, so a run killed midway leaves
    complete files rather than truncated ones the API would choke on.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{envelope.id}.json"
    # Dot-prefixed so load_index's glob cannot pick it up mid-write.
    temporary = out_dir / f".{envelope.id}.json.tmp"

    try:
        temporary.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return target


async def ingest(
    paths: Iterable[str],
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    force: bool = False,
    escalate: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    client_factory: ClientFactory = llm.OpenRouterClient,
    category_resolver=DEFAULT_RESOLVER,
) -> IngestResult:
    """Extract every file that has changed and save the results. The main entry point.

    Runs several pages at once since the time goes on waiting for the model. One page
    failing is recorded and does not stop the others.
    """
    result = IngestResult()
    index = {} if force else load_index(out_dir)
    limit = asyncio.Semaphore(max(1, concurrency))
    claimed: dict[str, str] = {}  # product id -> the input file that produced it

    async def one(path: str) -> None:
        """Handle a single file: skip it, extract and save it, or record the failure."""
        try:
            html = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.failed.append((path, f"could not read: {exc}"))
            return

        if content_hash(html) in index:
            result.skipped.append(path)
            logger.info("skipping %s (unchanged)", path)
            return

        async with limit:
            # A client per product, not per run: per-product cost is a slice of one
            # client's records, which would otherwise pick up interleaved calls.
            client = client_factory()
            envelope = await extract(
                html,
                path,
                client,
                escalate=escalate,
                category_resolver=category_resolver,
            )

        # Two inputs claiming the same canonical URL are the same product, which is
        # what makes a re-crawl update in place. Within one run it would mean a
        # silent overwrite, so the first keeps the id and the clash is reported.
        if envelope.id in claimed:
            result.collisions.append((envelope.id, claimed[envelope.id], path))
            logger.warning(
                "%s resolves to the same product id as %s (same canonical URL); "
                "keeping the first and not writing this one",
                path,
                claimed[envelope.id],
            )
            return
        claimed[envelope.id] = path

        write_envelope(envelope, out_dir)
        result.extracted.append(envelope)
        logger.info(
            "extracted %s -> %s.json ($%.6f)", path, envelope.id, envelope.extraction.cost_usd
        )

    outcomes = await asyncio.gather(
        *(one(path) for path in paths), return_exceptions=True
    )

    # One malformed page must not take the batch with it.
    for path, outcome in zip(paths, outcomes):
        if isinstance(outcome, BaseException):
            logger.error("failed %s: %s", path, outcome)
            result.failed.append((path, str(outcome)))

    return result
