"""Ingest CLI.

Default behaviour extracts every HTML file in data/ and writes one JSON envelope
per product. Re-running is cheap: a file whose contents have not changed is
recognised by hash and skipped without a single model call.

The --dump-* flags run the deterministic layers only and cost nothing, which is
the quickest way to see what the pipeline sees on a page.

    uv run python main.py                          # ingest data/*.html
    uv run python main.py path/to/page.html        # ingest one file from anywhere
    uv run python main.py --force                  # re-extract, e.g. after a prompt change
    uv run python main.py --escalate               # re-read weakly extracted pages
    uv run python main.py --dump-candidates        # no model calls
"""

import argparse
import asyncio
import logging
from pathlib import Path

from extraction import images, ingest
from extraction.layers import meta
from extraction.dom import canonical_url, parse, semantic_html
from extraction.ingest import DEFAULT_CONCURRENCY, DEFAULT_OUT_DIR
from extraction.pipeline import build_bundle

logger = logging.getLogger(__name__)


def dump_candidates(paths: list[str], max_value_chars: int = 70) -> None:
    """Print field / tier / source / value per input file.

    The useful comparison is between files: a page with rich JSON-LD shows tier-A
    rows for nearly every field, while a page that ships none shows its price
    arriving from microdata instead. That contrast is the evidence that the
    layering is load-bearing rather than decorative.
    """
    for path in paths:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
        bundle, soup = build_bundle(html)

        print(f"\n{'=' * 100}")
        print(f"{path}  ({len(html):,} bytes)")
        print(f"canonical: {canonical_url(soup)}")
        print(f"{'=' * 100}")

        if not len(bundle):
            print("  (no candidates from any layer)")
            continue

        counts: dict[str, int] = {}
        for candidate in bundle:
            counts[candidate.tier.value] = counts.get(candidate.tier.value, 0) + 1
        print(
            f"  {len(bundle)} candidates by tier: "
            + ", ".join(f"{tier}={count}" for tier, count in sorted(counts.items()))
        )
        print(f"  {'-' * 96}")
        print(f"  {'field':<26} {'tier':<5} {'source':<34} value")
        print(f"  {'-' * 96}")

        for field in bundle.fields():
            for candidate in bundle.for_field(field):
                value = str(candidate.value)
                if len(value) > max_value_chars:
                    value = value[:max_value_chars] + "\u2026"
                print(
                    f"  {field:<26} {candidate.tier.value:<5} "
                    f"{candidate.source[:34]:<34} {value}"
                )


def dump_images(paths: list[str]) -> None:
    """Print the harvested, normalised image list per input file."""
    for path in paths:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
        bundle, soup = build_bundle(html)
        base = canonical_url(soup)
        urls = images.harvest(soup, bundle, base)

        print(f"\n{'=' * 100}")
        print(f"{path}  ({len(urls)} images, {len(soup.find_all('img'))} <img> in DOM)")
        print(f"base: {base}")
        print(f"{'=' * 100}")
        for index, url in enumerate(urls, start=1):
            print(f"  {index:>2}. {url}")
        if not urls:
            print("  (none)")


def dump_semantic(paths: list[str]) -> None:
    """Print the size reduction from semantic_html per file.

    These ratios are the measured basis for the cost estimates in the write-up:
    the prompt sent to the model is this, not the raw page.
    """
    print(f"{'file':<28} {'raw':>10} {'semantic':>10} {'ratio':>7} {'~tokens':>9}")
    print("-" * 68)
    ratios = []
    for path in paths:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
        soup = parse(html)
        serialised = semantic_html(soup)
        ratio = len(serialised) / len(html) if html else 1.0
        ratios.append(ratio)
        print(
            f"{path:<28} {len(html):>10,} {len(serialised):>10,} "
            f"{ratio:>6.1%} {len(serialised) // 4:>9,}"
        )
    if ratios:
        print("-" * 68)
        print(f"{'mean':<28} {'':>10} {'':>10} {sum(ratios) / len(ratios):>6.1%}")


def report(result: ingest.IngestResult, out_dir: Path) -> None:
    """Print what the run did, and what it implies at scale."""
    print(f"\n{'=' * 78}")
    for envelope in result.extracted:
        product = envelope.product
        flags = []
        if envelope.extraction.escalated:
            flags.append("escalated")
        weak = [
            field
            for field, tier in envelope.extraction.field_tiers.items()
            if tier >= "D" and field not in {"description", "key_features"}
        ]
        if weak:
            flags.append("weak: " + ",".join(sorted(weak)))
        print(f"  {envelope.id}  {product.name[:44]:<46} ${envelope.extraction.cost_usd:.6f}")
        print(
            f"  {'':<12}  {product.category.name[:44]:<46} "
            f"{len(product.image_urls)} images, {len(product.variants)} variants"
        )
        if flags:
            print(f"  {'':<12}  {'; '.join(flags)}")

    print(f"{'=' * 78}")
    print(
        f"  extracted {len(result.extracted)}   "
        f"skipped {len(result.skipped)}   failed {len(result.failed)}"
    )
    for path, error in result.failed:
        print(f"    ! {path}: {error}")
    for product_id, kept, dropped in result.collisions:
        print(
            f"    ! {dropped} shares product id {product_id} with {kept} "
            "(same canonical URL); not written"
        )

    usage = ingest.llm.USAGE_TOTAL
    print(f"  output    {out_dir}")
    print(f"  calls     {usage['calls']}")
    print(
        f"  tokens    in={usage['input_tokens']:,} out={usage['output_tokens']:,} "
        f"(reasoning={usage['reasoning_tokens']:,})"
    )
    print(f"  cost      ${usage['cost_usd']:.6f} this run")

    if result.extracted:
        per_product = result.product_cost
        calls_per_product = usage["calls"] / len(result.extracted)
        print(f"\n  per product   ${per_product:.6f}  ({calls_per_product:.1f} calls)")
        print(f"  1M products   ${per_product * 1_000_000:,.0f}")
        print(f"  10M products  ${per_product * 10_000_000:,.0f}")
        # Worth stating explicitly: the per-call extrapolation in the provided
        # usage logger counts queries, and a product is several queries.
        print(
            f"  (note: {calls_per_product:.1f} calls per product, so a per-call "
            "extrapolation understates this by about that factor)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract products from HTML files into JSON envelopes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=f"HTML files or globs (default: {ingest.DEFAULT_INPUT_GLOB})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-extract even if the source is unchanged (use after a prompt change)",
    )
    parser.add_argument(
        "--escalate",
        action="store_true",
        help="re-read weakly extracted pages with a stronger model (costs more)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"pages extracted in parallel (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--dump-candidates",
        action="store_true",
        help="print the candidate bundle per file and exit (no model calls)",
    )
    parser.add_argument(
        "--dump-images",
        action="store_true",
        help="print the harvested image list per file and exit (no model calls)",
    )
    parser.add_argument(
        "--dump-semantic",
        action="store_true",
        help="print semantic_html size reduction per file and exit (no model calls)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    paths = ingest.expand(args.paths)
    if not paths:
        parser.error(f"no input files matched {ingest.DEFAULT_INPUT_GLOB}")

    if args.dump_candidates or args.dump_images or args.dump_semantic:
        if args.dump_candidates:
            dump_candidates(paths)
        if args.dump_images:
            dump_images(paths)
        if args.dump_semantic:
            dump_semantic(paths)
        return

    result = asyncio.run(
        ingest.ingest(
            paths,
            args.out,
            force=args.force,
            escalate=args.escalate,
            concurrency=args.concurrency,
        )
    )
    report(result, args.out)


if __name__ == "__main__":
    main()
