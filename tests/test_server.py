"""The read API, against a temp directory of envelopes."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extraction.ingest import write_envelope
from models import (
    Category,
    ExtractedProduct,
    ExtractionMetadata,
    Price,
    Product,
    Variant,
    VariantAttribute,
    VariantOption,
)
from server.main import PRODUCTS_DIR_ENV, app, load_products

client = TestClient(app)


def envelope(
    product_id: str = "fb8c3cf8024fffb4",
    name: str = "Pilar Floor Lamp",
    *,
    images: list[str] | None = None,
    variants: list[Variant] | None = None,
) -> ExtractedProduct:
    variants = variants if variants is not None else []
    return ExtractedProduct(
        id=product_id,
        slug=name.lower().replace(" ", "-"),
        source_url=f"https://shop.test/p/{product_id}",
        source_file="data/x.html",
        content_hash="a" * 64,
        options=[VariantOption(name="Size", values=["S", "M"])] if variants else [],
        product=Product(
            name=name,
            price=Price(price=349.0, currency="USD", compare_at_price=None),
            description="A lamp.",
            key_features=["Terrazzo base"],
            image_urls=images if images is not None else ["https://cdn.test/a.jpg"],
            category=Category(name="Home & Garden > Lighting > Lamps"),
            brand="Article",
            colors=["White Terrazzo"],
            variants=variants,
        ),
        extraction=ExtractionMetadata(
            field_tiers={"price.price": "D"}, cost_usd=0.001019
        ),
    )


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """An output directory the API will read, pointed at by the environment."""
    directory = tmp_path / "products"
    directory.mkdir()
    monkeypatch.setenv(PRODUCTS_DIR_ENV, str(directory))
    return directory


# --- listing ---------------------------------------------------------------


def test_list_returns_grid_sized_summaries(catalogue):
    write_envelope(envelope(), catalogue)

    response = client.get("/products")
    assert response.status_code == 200

    (item,) = response.json()
    assert set(item) == {"id", "slug", "name", "brand", "price", "image_url"}
    assert item["id"] == "fb8c3cf8024fffb4"
    assert item["name"] == "Pilar Floor Lamp"
    assert item["brand"] == "Article"
    assert item["price"] == {"price": 349.0, "currency": "USD", "compare_at_price": None}
    assert item["image_url"] == "https://cdn.test/a.jpg"


def test_the_summary_omits_the_heavy_fields(catalogue):
    """A card does not need a 17-row variant matrix or every image URL."""
    variants = [
        Variant(option_values=[VariantAttribute(name="Size", value=str(size))])
        for size in range(20)
    ]
    write_envelope(envelope(images=[f"https://cdn.test/{n}.jpg" for n in range(12)], variants=variants), catalogue)

    (item,) = client.get("/products").json()
    assert "variants" not in item
    assert "image_urls" not in item
    assert "extraction" not in item
    assert item["image_url"] == "https://cdn.test/0.jpg"


def test_products_without_images_summarise_cleanly(catalogue):
    write_envelope(envelope(images=[]), catalogue)
    (item,) = client.get("/products").json()
    assert item["image_url"] is None


def test_listing_is_sorted_by_name_not_filename(catalogue):
    """Grid order must not shuffle between requests."""
    write_envelope(envelope("00000000000000ff", "Zebra Chair"), catalogue)
    write_envelope(envelope("ffffffffffffff00", "Apple Crate"), catalogue)
    write_envelope(envelope("aaaaaaaaaaaaaa11", "mango Lamp"), catalogue)

    names = [item["name"] for item in client.get("/products").json()]
    assert names == ["Apple Crate", "mango Lamp", "Zebra Chair"]

    assert names == [item["name"] for item in client.get("/products").json()]


def test_an_empty_directory_returns_an_empty_list(catalogue):
    """The frontend's empty state should render, not an error page."""
    response = client.get("/products")
    assert response.status_code == 200
    assert response.json() == []


def test_a_missing_directory_returns_an_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv(PRODUCTS_DIR_ENV, str(tmp_path / "never-created"))
    assert client.get("/products").json() == []


def test_a_corrupt_envelope_costs_one_product_not_the_catalogue(catalogue):
    write_envelope(envelope(), catalogue)
    (catalogue / "broken.json").write_text("{ not json")

    response = client.get("/products")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_non_json_files_are_ignored(catalogue):
    write_envelope(envelope(), catalogue)
    (catalogue / "notes.txt").write_text("scratch")
    assert len(client.get("/products").json()) == 1


# --- detail ----------------------------------------------------------------


def test_detail_returns_the_full_envelope(catalogue):
    variants = [Variant(option_values=[VariantAttribute(name="Size", value="S")])]
    write_envelope(envelope(variants=variants), catalogue)

    response = client.get("/products/fb8c3cf8024fffb4")
    assert response.status_code == 200

    body = response.json()
    assert body["product"]["name"] == "Pilar Floor Lamp"
    assert body["product"]["image_urls"] == ["https://cdn.test/a.jpg"]
    assert body["options"] == [{"name": "Size", "values": ["S", "M"]}]
    assert body["extraction"]["field_tiers"] == {"price.price": "D"}
    assert body["source_url"] == "https://shop.test/p/fb8c3cf8024fffb4"


def test_detail_round_trips_into_the_model(catalogue):
    original = envelope()
    write_envelope(original, catalogue)
    body = client.get(f"/products/{original.id}").json()
    assert ExtractedProduct.model_validate(body) == original


def test_an_unknown_but_well_formed_id_is_a_clean_404(catalogue):
    write_envelope(envelope(), catalogue)
    response = client.get("/products/0123456789abcdef")
    assert response.status_code == 404
    assert "0123456789abcdef" in response.json()["detail"]


def test_detail_on_an_empty_catalogue_is_a_404(catalogue):
    assert client.get("/products/0123456789abcdef").status_code == 404


# --- id handling -----------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "fb8c3cf8024fffb4.json",
        "FB8C3CF8024FFFB4",  # ids are lower-case hex
        "short",
        "zzzzzzzzzzzzzzzz",
        "fb8c3cf8024fffb44",  # one character too long
    ],
)
def test_malformed_ids_are_rejected_before_any_lookup(catalogue, bad_id):
    """Defence in depth: the id is also never used to build a path.

    get_product filters the loaded products rather than opening
    products_dir() / f"{id}.json", so a traversal string could not become a file
    read even without the pattern constraint.
    """
    response = client.get(f"/products/{bad_id}")
    assert response.status_code in {404, 422}
    assert "passwd" not in response.text


def test_a_traversal_id_cannot_read_a_file_outside_the_directory(tmp_path, monkeypatch):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"id": "leaked"}))

    directory = tmp_path / "products"
    directory.mkdir()
    monkeypatch.setenv(PRODUCTS_DIR_ENV, str(directory))

    response = client.get("/products/..%2Fsecret")
    assert response.status_code in {404, 422}
    assert "leaked" not in response.text


# --- reading per request ---------------------------------------------------


def test_a_newly_ingested_product_appears_without_a_restart(catalogue):
    """The reason the directory is read per request rather than cached at startup."""
    assert client.get("/products").json() == []

    write_envelope(envelope(), catalogue)
    assert len(client.get("/products").json()) == 1

    write_envelope(envelope("0000000000000001", "Second Widget"), catalogue)
    assert len(client.get("/products").json()) == 2

    assert client.get("/products/0000000000000001").status_code == 200


def test_an_updated_product_is_served_fresh(catalogue):
    write_envelope(envelope(), catalogue)
    assert client.get("/products/fb8c3cf8024fffb4").json()["product"]["name"] == "Pilar Floor Lamp"

    write_envelope(envelope(name="Renamed Lamp"), catalogue)
    assert client.get("/products/fb8c3cf8024fffb4").json()["product"]["name"] == "Renamed Lamp"


# --- loader ----------------------------------------------------------------


def test_load_products_accepts_an_explicit_directory(tmp_path):
    directory = tmp_path / "elsewhere"
    directory.mkdir()
    write_envelope(envelope(), directory)
    assert len(load_products(directory)) == 1


def test_the_openapi_schema_documents_both_endpoints():
    schema = client.get("/openapi.json").json()
    assert "/products" in schema["paths"]
    assert "/products/{product_id}" in schema["paths"]
    assert "unauthenticated" in schema["info"]["description"].casefold()
