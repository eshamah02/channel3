import { describe, expect, it } from "vitest";
import {
  findVariant,
  initialSelection,
  isCombinationComplete,
  isValueAvailable,
  resolve,
} from "@/lib/variants";
import type { Price, Product, Variant, VariantOption } from "@/types";

const price = (over: Partial<Price> = {}): Price => ({
  price: 100,
  currency: "USD",
  compare_at_price: null,
  ...over,
});

/** A variant naming every axis, as declared data provides. */
const combo = (pairs: Record<string, string>, over: Partial<Variant> = {}): Variant => ({
  option_values: Object.entries(pairs).map(([name, value]) => ({ name, value })),
  sku: null,
  price: null,
  in_stock: null,
  image_urls: [],
  ...over,
});

const axis = (name: string, values: string[]): VariantOption => ({ name, values });

const product = (over: Partial<Product> = {}): Product => ({
  name: "Thing",
  price: price(),
  description: "",
  key_features: [],
  image_urls: ["https://cdn.test/product.jpg"],
  video_url: null,
  category: { name: "Home & Garden" },
  brand: "Brand",
  colors: [],
  variants: [],
  ...over,
});

// Shaped like the declared sample product: one colour, several sizes, every
// variant naming both axes.
const completeOptions = [axis("Color", ["Black"]), axis("Size", ["S", "M", "L"])];
const completeVariants = [
  combo({ Color: "Black", Size: "S" }, { price: price({ price: 10 }), in_stock: true }),
  combo({ Color: "Black", Size: "M" }, { price: price({ price: 12 }), in_stock: false }),
  // L is deliberately absent: a real gap in the matrix.
];

// Shaped like the observed sample product: axes read off the page's pickers, so
// each variant names one axis and no combination is asserted.
const observedOptions = [axis("Color", ["Black", "White"]), axis("Size", ["S", "M"])];
const observedVariants = [
  combo({ Color: "Black" }),
  combo({ Color: "White" }),
  combo({ Size: "S" }),
  combo({ Size: "M" }),
];

describe("isCombinationComplete", () => {
  it("is true when every variant names every axis", () => {
    expect(isCombinationComplete(completeOptions, completeVariants)).toBe(true);
  });

  it("is false for single-axis observations across two axes", () => {
    // The case that matters: treating these as combinations would disable
    // everything on the page.
    expect(isCombinationComplete(observedOptions, observedVariants)).toBe(false);
  });

  it("is true for a single axis with one value per variant", () => {
    expect(
      isCombinationComplete([axis("Size", ["44", "46"])], [combo({ Size: "44" }), combo({ Size: "46" })]),
    ).toBe(true);
  });

  it("is false with no axes or no variants", () => {
    expect(isCombinationComplete([], [])).toBe(false);
    expect(isCombinationComplete(completeOptions, [])).toBe(false);
    expect(isCombinationComplete([], completeVariants)).toBe(false);
  });

  it("is false when a variant names an axis that is not in the options", () => {
    expect(
      isCombinationComplete([axis("Size", ["S"])], [combo({ Fit: "Tall" })]),
    ).toBe(false);
  });
});

describe("findVariant", () => {
  it("matches a full selection", () => {
    const found = findVariant(completeVariants, { Color: "Black", Size: "M" });
    expect(found?.price?.price).toBe(12);
  });

  it("returns null for a combination that does not exist", () => {
    expect(findVariant(completeVariants, { Color: "Black", Size: "L" })).toBeNull();
  });

  it("returns null for a partial selection", () => {
    // Half a selection must not silently resolve to some variant.
    expect(findVariant(completeVariants, { Size: "M" })).toBeNull();
  });

  it("returns null for an empty selection", () => {
    expect(findVariant(completeVariants, {})).toBeNull();
  });
});

describe("isValueAvailable", () => {
  it("reports a real combination as available", () => {
    expect(
      isValueAvailable(completeVariants, { Color: "Black", Size: "S" }, "Size", "M"),
    ).toBe(true);
  });

  it("reports a gap in the matrix as unavailable", () => {
    expect(
      isValueAvailable(completeVariants, { Color: "Black", Size: "S" }, "Size", "L"),
    ).toBe(false);
  });
});

describe("initialSelection", () => {
  it("opens on a combination that exists", () => {
    // The first value of each axis can name a combination nobody sells, since
    // axes are the union across variants.
    const variants = [combo({ Color: "White", Size: "M" })];
    const options = [axis("Color", ["Black", "White"]), axis("Size", ["S", "M"])];
    expect(initialSelection(options, variants)).toEqual({ Color: "White", Size: "M" });
  });

  it("falls back to the first value of each axis for observations", () => {
    expect(initialSelection(observedOptions, observedVariants)).toEqual({
      Color: "Black",
      Size: "S",
    });
  });

  it("is empty with no axes", () => {
    expect(initialSelection([], [])).toEqual({});
  });

  it("skips an axis with no values", () => {
    expect(initialSelection([axis("Size", [])], [])).toEqual({});
  });
});

describe("resolve", () => {
  it("applies the selected variant's price", () => {
    const item = product({ variants: completeVariants });
    const { price: resolved } = resolve(
      item,
      completeVariants,
      { Color: "Black", Size: "M" },
      true,
    );
    expect(resolved.price).toBe(12);
  });

  it("falls back to the product price when the variant has none", () => {
    const variants = [combo({ Size: "44" })];
    const item = product({ price: price({ price: 170 }), variants });
    expect(resolve(item, variants, { Size: "44" }, true).price.price).toBe(170);
  });

  it("prefers variant images when it has them", () => {
    const variants = [
      combo({ Size: "S" }, { image_urls: ["https://cdn.test/variant.jpg"] }),
    ];
    const item = product({ variants });
    expect(resolve(item, variants, { Size: "S" }, true).images).toEqual([
      "https://cdn.test/variant.jpg",
    ]);
  });

  it("falls back to product images when the variant has none", () => {
    const variants = [combo({ Size: "S" })];
    const item = product({ variants });
    expect(resolve(item, variants, { Size: "S" }, true).images).toEqual([
      "https://cdn.test/product.jpg",
    ]);
  });

  it("passes through variant stock, including false", () => {
    const item = product({ variants: completeVariants });
    expect(resolve(item, completeVariants, { Color: "Black", Size: "S" }, true).inStock).toBe(true);
    expect(resolve(item, completeVariants, { Color: "Black", Size: "M" }, true).inStock).toBe(false);
  });

  it("reports unknown stock rather than guessing", () => {
    // No page in the sample catalogue publishes per-variant availability.
    const variants = [combo({ Size: "S" })];
    const item = product({ variants });
    expect(resolve(item, variants, { Size: "S" }, true).inStock).toBeNull();
  });

  it("never resolves a variant when the data is observations", () => {
    const item = product({ variants: observedVariants });
    const resolved = resolve(item, observedVariants, { Color: "Black", Size: "S" }, false);
    expect(resolved.variant).toBeNull();
    expect(resolved.price.price).toBe(100);
    expect(resolved.inStock).toBeNull();
  });
});
