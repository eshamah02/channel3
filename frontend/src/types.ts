/**
 * Mirrors ProductSummary and ExtractedProduct in models.py by hand. Two endpoints
 * is below the threshold where an OpenAPI codegen step pays for itself.
 */

export interface Price {
  price: number;
  currency: string;
  compare_at_price: number | null;
}

export interface VariantAttribute {
  name: string;
  value: string;
}

export interface Variant {
  option_values: VariantAttribute[];
  sku: string | null;
  price: Price | null;
  in_stock: boolean | null;
  image_urls: string[];
}

/** One selectable axis, derived in Python from the variant list. */
export interface VariantOption {
  name: string;
  values: string[];
}

export interface Category {
  name: string;
}

export interface Product {
  name: string;
  price: Price;
  description: string;
  key_features: string[];
  image_urls: string[];
  video_url: string | null;
  category: Category;
  brand: string;
  colors: string[];
  variants: Variant[];
}

export interface ExtractionMetadata {
  /** Field name to provenance tier, A (site-declared) through E (model inferred). */
  field_tiers: Record<string, string>;
  escalated: boolean;
  warnings: string[];
  cost_usd: number;
}

export interface ExtractedProduct {
  id: string;
  slug: string;
  source_url: string | null;
  source_file: string;
  content_hash: string;
  options: VariantOption[];
  product: Product;
  extraction: ExtractionMetadata;
}

/** The grid-sized view: no variant matrix, one image. */
export interface ProductSummary {
  id: string;
  slug: string;
  name: string;
  brand: string;
  price: Price;
  image_url: string | null;
}
