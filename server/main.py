"""Read-only API over the per-product JSON files that ingest writes.

The directory is read on every request, which is what makes a newly ingested product
appear on refresh with no restart. That is microseconds for tens of products and
untenable for a million: at catalogue scale these envelopes belong in a database and
this layer becomes a query, which is the non-scaling assumption the README names.

The API is unauthenticated and read-only by design -- localhost-bound, no mutating
endpoints, serving only data extracted from public pages. Exposing it publicly would
need an auth layer and rate limiting at minimum.

Product ids never touch the filesystem: a detail lookup filters the already-loaded
products rather than building a path from the URL, and the id is constrained to the
16 hex characters make_id produces.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam
from pydantic import ValidationError

from models import ExtractedProduct, ProductSummary

logger = logging.getLogger(__name__)

PRODUCTS_DIR_ENV = "CHANNEL3_PRODUCTS_DIR"
DEFAULT_PRODUCTS_DIR = Path("out/products")

# make_id returns the first 16 characters of a sha256 hex digest.
ID_PATTERN = r"^[0-9a-f]{16}$"

app = FastAPI(
    title="Channel3 catalogue",
    version="0.1.0",
    description=(
        "Read-only access to extracted products. Unauthenticated by design: "
        "localhost-bound, no mutating endpoints, and serving only data extracted "
        "from public product pages."
    ),
)


def products_dir() -> Path:
    """Where ingest wrote its output, read from the environment on each call."""
    return Path(os.environ.get(PRODUCTS_DIR_ENV) or DEFAULT_PRODUCTS_DIR)


def load_products(directory: Path | None = None) -> list[ExtractedProduct]:
    """Every envelope in the output directory, in a stable order.

    A missing directory means nothing has been ingested yet, not an error. One
    unreadable envelope costs one product rather than the whole catalogue.
    """
    directory = directory or products_dir()
    if not directory.is_dir():
        return []

    products: list[ExtractedProduct] = []
    for path in sorted(directory.glob("*.json")):
        try:
            products.append(ExtractedProduct.model_validate_json(path.read_text()))
        except (ValidationError, ValueError, OSError) as exc:
            logger.warning("skipping unreadable envelope %s: %s", path, exc)

    # Sorted by name so the grid does not reshuffle; id breaks ties.
    products.sort(key=lambda item: (item.product.name.casefold(), item.id))
    return products


def summarise(envelope: ExtractedProduct) -> ProductSummary:
    return ProductSummary(
        id=envelope.id,
        slug=envelope.slug,
        name=envelope.product.name,
        brand=envelope.product.brand,
        price=envelope.product.price,
        image_url=envelope.product.image_urls[0] if envelope.product.image_urls else None,
    )


@app.get("/products", response_model=list[ProductSummary], summary="List products")
def list_products() -> list[ProductSummary]:
    """Grid-sized summaries. Empty list when nothing has been ingested."""
    return [summarise(envelope) for envelope in load_products()]


@app.get(
    "/products/{product_id}",
    response_model=ExtractedProduct,
    summary="Get one product",
    responses={404: {"description": "No product with that id"}},
)
def get_product(
    product_id: str = PathParam(
        ...,
        pattern=ID_PATTERN,
        description="16 hex characters, as produced by make_id",
        examples=["fb8c3cf8024fffb4"],
    ),
) -> ExtractedProduct:
    """The full envelope, including the derived variant axes the PDP renders."""
    for envelope in load_products():
        if envelope.id == product_id:
            return envelope
    raise HTTPException(status_code=404, detail=f"No product with id {product_id}")
