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

    A name/value pair rather than dict[str, str] because this comes back from the
    model as structured output, and JSON Schema handles fixed shapes far more
    reliably than open-ended maps. Axis names come from the page, never a fixed list.
    """

    name: str
    value: str


class Variant(BaseModel):
    """One discrete, purchasable configuration of a product.

    Only option_values is required: some pages publish a full per-SKU price and
    stock matrix, others only the selectable values.
    """

    option_values: list[VariantAttribute]
    sku: str | None = None
    price: Price | None = None
    in_stock: bool | None = None
    image_urls: list[str] = Field(default_factory=list)


class VariantOption(BaseModel):
    """One selectable axis and its values, e.g. name="Size", values=["S","M"].

    Derived from the variant list rather than generated: two independent
    representations of the same facts can disagree, one derived from the other cannot.
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


class VariantAxis(BaseModel):
    """One selectable axis after the model has tidied it up.

    Used only when the matrix was read off the page's own controls, where labels are
    often plural or generic and the same axis can appear under two headings. Same
    shape as VariantOption but kept separate: this one is model input, arriving before
    variants exist, while VariantOption is derived from the finished variant list.
    """

    name: str
    values: list[str]


# Declared without defaults and with explicit nullability, because strict structured
# output requires every property to be present in the response.
class FactsResponse(BaseModel):
    """The judgement call: choose between candidates and normalise formats.

    price is flat rather than a nested Price so the compare-at invariants can be
    enforced in Python. Note the omission of image_urls: those are harvested
    deterministically and the model never gets to rewrite them.
    """

    name: str
    brand: str
    price: float
    currency: str
    compare_at_price: float | None
    colors: list[str]
    axes: list[VariantAxis]
    video_url: str | None


class ProseResponse(BaseModel):
    """Synthesis, not extraction. No standard carries either of these fields."""

    description: str
    key_features: list[str]


class CategoryChoice(BaseModel):
    """One step of the taxonomy descent, as an index into offered options.

    An index rather than a name: the model picks from a list of real children, so
    it has no way to return a string that is not in the taxonomy.
    """

    choice: int


class ExtractionMetadata(BaseModel):
    """How this product was extracted, not what it is.

    Kept off Product so that schema stays as delivered. A price from JSON-LD is a
    different claim from one read off rendered text, and this is where that shows.
    """

    # field name -> provenance tier of the value that won, e.g. {"price.price": "A"}
    field_tiers: dict[str, str] = Field(default_factory=dict)
    escalated: bool = False
    warnings: list[str] = Field(default_factory=list)
    cost_usd: float = 0.0


class ProductSummary(BaseModel):
    """Grid-sized view of a product, for the catalogue listing.

    A card needs a name, a price and one image; shipping the full variant matrix per
    card makes the list response many times larger than the page can use.
    """

    id: str
    slug: str
    name: str
    brand: str
    price: Price
    image_url: str | None = None


class ExtractedProduct(BaseModel):
    """Envelope around an unmodified Product: identity, provenance, derived axes."""

    # Truncated sha256 of the canonical URL. Hashing the URL rather than the content
    # keeps the id stable across re-crawls and needs no global coordination.
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