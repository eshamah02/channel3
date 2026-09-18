import type { Price, Product, Variant, VariantOption } from "@/types";

/**
 * Variant resolution. A complete matrix names every axis on every variant, so a
 * selection resolves to one and a missing combination is a real gap. Axis
 * observations name one axis each, where a two-axis selection can never match and
 * the matching rule would disable every option, so availability is gated on shape.
 */

export type Selection = Record<string, string>;

/** True when every variant names every axis, so a selection can resolve to one. */
export function isCombinationComplete(
  options: VariantOption[],
  variants: Variant[],
): boolean {
  if (options.length === 0 || variants.length === 0) return false;
  const axes = new Set(options.map((option) => option.name));
  return variants.every(
    (variant) =>
      variant.option_values.length === axes.size &&
      variant.option_values.every((pair) => axes.has(pair.name)),
  );
}

/** The variant matching every selected value exactly, or null. */
export function findVariant(variants: Variant[], selection: Selection): Variant | null {
  const entries = Object.entries(selection);
  if (entries.length === 0) return null;

  return (
    variants.find(
      (variant) =>
        variant.option_values.length === entries.length &&
        entries.every(([name, value]) =>
          variant.option_values.some((pair) => pair.name === name && pair.value === value),
        ),
    ) ?? null
  );
}

/**
 * Whether choosing `value` on `axis` leads to a real combination. Only meaningful
 * for a complete matrix, where absence means unavailable rather than unknown.
 */
export function isValueAvailable(
  variants: Variant[],
  selection: Selection,
  axis: string,
  value: string,
): boolean {
  return findVariant(variants, { ...selection, [axis]: value }) !== null;
}

/**
 * An opening selection that is valid rather than merely first. Axes are the union
 * across variants, so the first value of each can name a combination nobody sells.
 */
export function initialSelection(
  options: VariantOption[],
  variants: Variant[],
): Selection {
  if (isCombinationComplete(options, variants)) {
    return Object.fromEntries(
      variants[0].option_values.map((pair) => [pair.name, pair.value]),
    );
  }
  return Object.fromEntries(
    options
      .filter((option) => option.values.length > 0)
      .map((option) => [option.name, option.values[0]]),
  );
}

export interface ResolvedVariant {
  variant: Variant | null;
  price: Price;
  images: string[];
  /** true, false, or null when the page never published availability. */
  inStock: boolean | null;
}

/** Apply the selected variant's overrides on top of the product's own values. */
export function resolve(
  product: Product,
  variants: Variant[],
  selection: Selection,
  complete: boolean,
): ResolvedVariant {
  const variant = complete ? findVariant(variants, selection) : null;

  return {
    variant,
    // A per-variant price is the price of that SKU; without one the product's
    // own price stands.
    price: variant?.price ?? product.price,
    images:
      variant && variant.image_urls.length > 0 ? variant.image_urls : product.image_urls,
    inStock: variant?.in_stock ?? null,
  };
}
