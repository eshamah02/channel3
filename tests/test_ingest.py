"""Batch ingest: caching, concurrency, atomic writes, failure isolation.

All offline. The point of the client factory is that a run can be driven entirely
by fakes, so the caching and failure behaviour is verifiable without spending
anything.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from extraction import ingest, llm
from extraction.ingest import expand, ingest as run_ingest, load_index, write_envelope
from extraction.llm import FakeLLMClient, UsageRecord
from models import (
    Category,
    ExtractedProduct,
    ExtractionMetadata,
    FactsResponse,
    Price,
    Product,
    ProseResponse,
)

FIXTURES = Path(__file__).parent / "fixtures"


def facts(**overrides) -> FactsResponse:
    defaults = dict(
        name="Structured Widget",
        brand="Fixture Brand",
        price=49.95,
        currency="USD",
        compare_at_price=None,
        colors=[],
        axes=[],
        video_url=None,
    )
    return FactsResponse(**{**defaults, **overrides})


def prose() -> ProseResponse:
    return ProseResponse(description="A widget.", key_features=["Durable"])


def envelope(product_id: str = "abc123", content: str = "hash-1") -> ExtractedProduct:
    return ExtractedProduct(
        id=product_id,
        slug="widget",
        source_file="data/x.html",
        content_hash=content,
        product=Product(
            name="Widget",
            price=Price(price=1.0, currency="USD"),
            description="d",
            key_features=["f"],
            image_urls=["https://cdn.test/a.jpg"],
            category=Category(name="Home & Garden"),
            brand="B",
            colors=[],
            variants=[],
        ),
        extraction=ExtractionMetadata(cost_usd=0.001),
    )


class Factory:
    """Hands out one fake client per product, as the real run does."""

    def __init__(self, per_product: int = 1) -> None:
        self.per_product = per_product
        self.clients: list[FakeLLMClient] = []

    def __call__(self) -> FakeLLMClient:
        client = FakeLLMClient([facts(), prose()])
        self.clients.append(client)
        return client

    @property
    def calls(self) -> int:
        return sum(len(client.calls) for client in self.clients)


@pytest.fixture(autouse=True)
def _clean_usage():
    llm.reset_usage_total()
    yield
    llm.reset_usage_total()


def pages(tmp_path: Path, count: int = 2) -> list[str]:
    """Distinct HTML files, so their content hashes differ."""
    source = (FIXTURES / "jsonld_product.html").read_text()
    paths = []
    for index in range(count):
        path = tmp_path / f"page{index}.html"
        path.write_text(source.replace("Structured Widget", f"Widget {index}"))
        paths.append(str(path))
    return paths


async def go(paths, out_dir, *, factory=None, **kwargs):
    """Run an ingest against fakes.

    Category resolution is off: these tests are about caching, concurrency and
    file handling, and the descent would need several queued choices per product
    in every one of them. tests/test_category.py covers the descent itself.
    """
    factory = factory or Factory()
    kwargs.setdefault("category_resolver", None)
    result = await run_ingest(paths, out_dir, client_factory=factory, **kwargs)
    return result, factory


# --- path expansion --------------------------------------------------------


def test_expand_defaults_to_the_sample_directory():
    assert expand([]) == sorted(__import__("glob").glob(ingest.DEFAULT_INPUT_GLOB))
    assert expand(None) == expand([])


def test_expand_handles_globs_and_literal_paths(tmp_path):
    first = tmp_path / "a.html"
    second = tmp_path / "b.html"
    first.write_text("<html></html>")
    second.write_text("<html></html>")

    assert expand([str(tmp_path / "*.html")]) == [str(first), str(second)]
    assert expand([str(second)]) == [str(second)]


def test_expand_drops_duplicates_preserving_order(tmp_path):
    path = tmp_path / "a.html"
    path.write_text("<html></html>")
    assert expand([str(path), str(path)]) == [str(path)]


# --- the index -------------------------------------------------------------


def test_load_index_maps_content_hash_to_id(tmp_path):
    write_envelope(envelope("id-one", "hash-one"), tmp_path)
    write_envelope(envelope("id-two", "hash-two"), tmp_path)
    assert load_index(tmp_path) == {"hash-one": "id-one", "hash-two": "id-two"}


def test_load_index_on_a_missing_directory_is_empty(tmp_path):
    assert load_index(tmp_path / "nope") == {}


def test_a_corrupt_envelope_is_ignored_not_fatal(tmp_path):
    """Refusing to start would be worse than re-extracting one page."""
    write_envelope(envelope("good", "hash-good"), tmp_path)
    (tmp_path / "broken.json").write_text("{ not json")

    assert load_index(tmp_path) == {"hash-good": "good"}


# --- atomic writes ---------------------------------------------------------


def test_write_is_atomic_and_leaves_no_temporary(tmp_path):
    target = write_envelope(envelope("id-one", "h"), tmp_path)
    assert target.name == "id-one.json"
    assert json.loads(target.read_text())["id"] == "id-one"
    assert list(tmp_path.iterdir()) == [target]


def test_an_interrupted_write_leaves_no_partial_file(tmp_path, monkeypatch):
    """A truncated envelope would be served by the API and fail to parse."""
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        write_envelope(envelope("id-one", "h"), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_rewrite_replaces_rather_than_appends(tmp_path):
    write_envelope(envelope("id-one", "h1"), tmp_path)
    write_envelope(envelope("id-one", "h2"), tmp_path)
    assert json.loads((tmp_path / "id-one.json").read_text())["content_hash"] == "h2"
    assert len(list(tmp_path.iterdir())) == 1


# --- caching ---------------------------------------------------------------


async def test_a_first_run_extracts_and_writes_one_file_per_product(tmp_path):
    out = tmp_path / "out"
    result, factory = await go(pages(tmp_path, 2), out)

    assert len(result.extracted) == 2
    assert result.skipped == []
    assert len(list(out.glob("*.json"))) == 2
    assert factory.calls == 4  # facts + prose per product


async def test_a_second_run_over_unchanged_input_makes_no_model_calls(tmp_path):
    out = tmp_path / "out"
    paths = pages(tmp_path, 2)
    await go(paths, out)

    llm.reset_usage_total()
    result, factory = await go(paths, out)

    assert result.extracted == []
    assert len(result.skipped) == 2
    assert factory.calls == 0
    assert llm.USAGE_TOTAL["cost_usd"] == 0.0


async def test_force_re_extracts_unchanged_input(tmp_path):
    """What you use after changing a prompt: the HTML is the same, the logic is not."""
    out = tmp_path / "out"
    paths = pages(tmp_path, 2)
    await go(paths, out)

    result, factory = await go(paths, out, force=True)

    assert len(result.extracted) == 2
    assert result.skipped == []
    assert factory.calls == 4


async def test_only_the_new_file_is_extracted_on_a_later_run(tmp_path):
    """The workflow the whole task exists for."""
    out = tmp_path / "out"
    paths = pages(tmp_path, 2)
    await go(paths, out)

    third = tmp_path / "page2.html"
    third.write_text(
        (FIXTURES / "jsonld_product.html").read_text().replace(
            "Structured Widget", "A Third Widget"
        )
    )

    llm.reset_usage_total()
    result, factory = await go(paths + [str(third)], out)

    assert len(result.extracted) == 1
    assert len(result.skipped) == 2
    assert factory.calls == 2
    assert len(list(out.glob("*.json"))) == 3


async def test_an_edited_file_is_re_extracted(tmp_path):
    """Caching is on content, not filename, so an edit is a cache miss."""
    out = tmp_path / "out"
    paths = pages(tmp_path, 1)
    await go(paths, out)

    Path(paths[0]).write_text(
        (FIXTURES / "jsonld_product.html").read_text().replace(
            "Structured Widget", "Edited Widget"
        )
    )
    result, _ = await go(paths, out)
    assert len(result.extracted) == 1


# --- failure isolation ----------------------------------------------------


async def test_an_unreadable_file_is_reported_without_stopping_the_batch(tmp_path):
    out = tmp_path / "out"
    good = pages(tmp_path, 1)
    missing = str(tmp_path / "does-not-exist.html")

    result, _ = await go(good + [missing], out)

    assert len(result.extracted) == 1
    assert len(result.failed) == 1
    assert "could not read" in result.failed[0][1]


async def test_a_page_that_fails_extraction_is_reported_and_others_succeed(tmp_path):
    """One malformed page must not take a batch of fifty million with it."""
    out = tmp_path / "out"
    paths = pages(tmp_path, 3)

    class PartlyBroken(Factory):
        def __call__(self):
            client = FakeLLMClient(
                [] if len(self.clients) == 1 else [facts(), prose()]
            )
            self.clients.append(client)
            return client

    result, _ = await go(paths, out, factory=PartlyBroken())

    assert len(result.extracted) == 2
    assert len(result.failed) == 1
    assert len(list(out.glob("*.json"))) == 2


async def test_a_failed_page_writes_nothing(tmp_path):
    out = tmp_path / "out"

    class AlwaysBroken(Factory):
        def __call__(self):
            client = FakeLLMClient([])
            self.clients.append(client)
            return client

    result, _ = await go(pages(tmp_path, 1), out, factory=AlwaysBroken())

    assert result.extracted == []
    assert len(result.failed) == 1
    assert not out.exists() or list(out.glob("*.json")) == []


# --- concurrency ----------------------------------------------------------


class CountingFactory(Factory):
    """Tracks how many extractions are in flight at once."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0

    def __call__(self):
        factory = self

        class Counting(FakeLLMClient):
            async def parse(self, model, input, text_format, **kwargs):
                factory.in_flight += 1
                factory.peak = max(factory.peak, factory.in_flight)
                await asyncio.sleep(0)
                try:
                    return await super().parse(model, input, text_format, **kwargs)
                finally:
                    factory.in_flight -= 1

        client = Counting([facts(), prose()])
        self.clients.append(client)
        return client


async def test_concurrency_is_bounded_by_the_semaphore(tmp_path):
    out = tmp_path / "out"
    factory = CountingFactory()
    await go(pages(tmp_path, 6), out, factory=factory, concurrency=2)
    assert factory.peak <= 2


async def test_concurrency_of_one_serialises(tmp_path):
    out = tmp_path / "out"
    factory = CountingFactory()
    await go(pages(tmp_path, 4), out, factory=factory, concurrency=1)
    assert factory.peak == 1


async def test_every_product_gets_its_own_client(tmp_path):
    """Per-product cost is a sum over one client's records.

    Sharing a client across concurrent extractions would bill each product for
    whatever the others happened to be doing at the same time.
    """
    out = tmp_path / "out"
    result, factory = await go(pages(tmp_path, 3), out, concurrency=3)

    assert len(factory.clients) == 3
    for client in factory.clients:
        assert len(client.calls) == 2
    assert len(result.extracted) == 3


async def test_per_product_cost_excludes_other_products(tmp_path):
    """A client carrying a previous product's records must not leak into this one."""
    out = tmp_path / "out"

    class Preloaded(Factory):
        def __call__(self):
            client = FakeLLMClient([facts(), prose()])
            client.records.append(UsageRecord("prior/model", 9, 9, 0, 1.23))
            self.clients.append(client)
            return client

    result, _ = await go(pages(tmp_path, 2), out, factory=Preloaded())
    for item in result.extracted:
        assert item.extraction.cost_usd == 0.0


# --- results --------------------------------------------------------------


async def test_result_reports_mean_product_cost(tmp_path):
    out = tmp_path / "out"
    result, _ = await go(pages(tmp_path, 2), out)
    assert result.product_cost == 0.0  # the fake reports zero per call
    assert llm.USAGE_TOTAL["calls"] == 4


async def test_written_envelopes_round_trip(tmp_path):
    out = tmp_path / "out"
    result, _ = await go(pages(tmp_path, 1), out)

    path = out / f"{result.extracted[0].id}.json"
    reloaded = ExtractedProduct.model_validate_json(path.read_text())
    assert reloaded == result.extracted[0]


async def test_two_inputs_sharing_a_canonical_url_collide_visibly(tmp_path):
    """Identity is the hash of the canonical URL, so this is the same product.

    That is deliberate -- it is what makes a re-crawl update in place rather than
    duplicate -- but within one run it would mean one input silently overwriting
    another, which is worth saying out loud.
    """
    out = tmp_path / "out"
    source = (FIXTURES / "meta_only.html").read_text()  # carries link rel=canonical

    first = tmp_path / "one.html"
    second = tmp_path / "two.html"
    first.write_text(source)
    second.write_text(source.replace("Meta Widget", "Edited Widget"))

    result, _ = await go([str(first), str(second)], out)

    assert len(result.extracted) == 1
    assert len(result.collisions) == 1
    product_id, kept, dropped = result.collisions[0]
    assert kept == str(first)
    assert dropped == str(second)
    assert len(list(out.glob("*.json"))) == 1


async def test_distinct_canonical_urls_produce_distinct_files(tmp_path):
    out = tmp_path / "out"
    source = (FIXTURES / "meta_only.html").read_text()

    first = tmp_path / "one.html"
    second = tmp_path / "two.html"
    first.write_text(source)
    second.write_text(source.replace("/products/meta-widget", "/products/other-widget"))

    result, _ = await go([str(first), str(second)], out)

    assert len(result.extracted) == 2
    assert result.collisions == []
    assert len(list(out.glob("*.json"))) == 2


async def test_re_ingesting_an_edited_page_updates_in_place(tmp_path):
    """Same URL, new content: one file, refreshed -- not a second product."""
    out = tmp_path / "out"
    source = (FIXTURES / "meta_only.html").read_text()
    path = tmp_path / "page.html"

    path.write_text(source)
    first, _ = await go([str(path)], out)

    path.write_text(source.replace("59.00", "49.00"))
    second, _ = await go([str(path)], out)

    assert first.extracted[0].id == second.extracted[0].id
    assert len(list(out.glob("*.json"))) == 1


async def test_output_filename_is_the_product_id(tmp_path):
    """The API serves this directory, so the filename has to be the lookup key."""
    out = tmp_path / "out"
    result, _ = await go(pages(tmp_path, 1), out)
    envelope_id = result.extracted[0].id
    assert (out / f"{envelope_id}.json").exists()
