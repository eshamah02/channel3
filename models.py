from pathlib import Path
from pydantic import BaseModel, Field, field_validator

# Load categories once at module level
CATEGORIES_FILE = Path(__file__).parent / "categories.txt"
VALID_CATEGORIES = set()
if CATEGORIES_FILE.exists():
    with open(CATEGORIES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                VALID_CATEGORIES.add(line)

class Category(BaseModel):
    # A category from Google's Product Taxonomy
    # https://www.google.com/basepages/producttype/taxonomy.en-US.txt
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_exists(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Category '{v}' is not a valid category in categories.txt")
        return v

class Price(BaseModel):
    price: float
    currency: str
    # If a product is on sale, this is the original price
    compare_at_price: float | None = None


class VariantAttribute(BaseModel):
    """One axis/value pair on a variant, e.g. name="Size", value="M".

    Modelled as an explicit name/value pair rather than dict[str, str] because
    this shape comes back from the model as structured output: JSON Schema
    describes fixed-shape objects reliably, while an open-ended map needs
    additionalProperties and generates inconsistently.

    Axis names are whatever the page uses -- Size, Color, Fit, Voltage, Finish.
    A fixed vocabulary would be a guess about the catalogue and would fail on
    anything that isn't apparel.
    """

    name: str
    value: str


class Variant(BaseModel):
    """One discrete, purchasable configuration of a product.

    Every field except option_values is optional because pages vary in what
    they publish per variant: some carry a full per-SKU price and stock matrix,
    others only list the selectable values.
    """

    option_values: list[VariantAttribute]
    sku: str | None = None
    price: Price | None = None
    in_stock: bool | None = None
    image_urls: list[str] = Field(default_factory=list)


class VariantOption(BaseModel):
    """One selectable axis and its values, e.g. name="Size", values=["S","M"].

    Derived from the variant list in Python (see extraction/derive.py), never
    generated. Two independently-produced representations of the same facts can
    disagree; one derived from the other cannot.
    """

    name: str
    values: list[str]


# This is the final product schema that you need to output. 
# You may add additional models as needed.
class Product(BaseModel):
    name: str
    price: Price
    description: str
    key_features: list[str]
    image_urls: list[str]
    video_url: str | None = None
    category: Category
    brand: str
    colors: list[str]
    variants: list[Variant]


class ExtractionMetadata(BaseModel):
    """How this product was extracted, not what it is.

    Kept off Product so their schema stays as delivered. Useful to a reviewer
    auditing extraction quality, and to a downstream agent that wants a
    confidence signal: a price sourced from JSON-LD (tier A) is a different
    claim than one read off rendered text (tier D).
    """

    # field name -> provenance tier of the value that won, e.g. {"price.price": "A"}
    field_tiers: dict[str, str] = Field(default_factory=dict)
    escalated: bool = False
    warnings: list[str] = Field(default_factory=list)
    cost_usd: float = 0.0


class ExtractedProduct(BaseModel):
    """Envelope around an unmodified Product.

    Everything the pipeline knows that isn't part of the product itself lives
    here: identity, provenance, and the derived variant axes.
    """

    # sha256 of the canonical URL, truncated. Hashing the URL rather than the
    # page content keeps the id stable across re-crawls -- a content hash would
    # mint a new id every time the site fixed a typo. It also needs no global
    # coordination, which slug-uniqueness would at catalogue scale.
    id: str
    slug: str
    source_url: str | None = None
    source_file: str
    # sha256 of the raw HTML, used by the ingest CLI to skip unchanged input.
    content_hash: str
    # Derived from product.variants; see derive_options().
    options: list[VariantOption] = Field(default_factory=list)
    product: Product
    extraction: ExtractionMetadata = Field(default_factory=ExtractionMetadata)