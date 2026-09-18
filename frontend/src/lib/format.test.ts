import { describe, expect, it } from "vitest";
import { discountPercent, formatPrice, isOnSale } from "@/lib/format";
import type { Price } from "@/types";

const price = (over: Partial<Price> = {}): Price => ({
  price: 29.95,
  currency: "USD",
  compare_at_price: null,
  ...over,
});

describe("formatPrice", () => {
  it("uses the product's own currency, not a hardcoded symbol", () => {
    // The sample catalogue really does mix these: four USD, one GBP.
    expect(formatPrice(76.99, "GBP")).toContain("76.99");
    expect(formatPrice(76.99, "GBP")).toContain("£");
    expect(formatPrice(129, "USD")).toContain("$");
    expect(formatPrice(129, "USD")).not.toContain("£");
  });

  it("formats whole amounts with minor units", () => {
    expect(formatPrice(349, "USD")).toBe("$349.00");
  });

  it("falls back readably for a currency Intl does not know", () => {
    // Currency can be inferred rather than declared, so it may be nonsense.
    expect(formatPrice(10, "NOTACURRENCY")).toBe("NOTACURRENCY 10.00");
  });
});

describe("isOnSale", () => {
  it("is false without a compare-at price", () => {
    expect(isOnSale(price())).toBe(false);
  });

  it("is true when the former price is higher", () => {
    expect(isOnSale(price({ price: 76.99, compare_at_price: 109.99 }))).toBe(true);
  });

  it("is false when the former price is not actually higher", () => {
    // The API already guards this, but a discount of nothing must never render.
    expect(isOnSale(price({ price: 29.95, compare_at_price: 29.95 }))).toBe(false);
    expect(isOnSale(price({ price: 40, compare_at_price: 30 }))).toBe(false);
  });
});

describe("discountPercent", () => {
  it("rounds to a whole percentage", () => {
    expect(discountPercent(price({ price: 76.99, compare_at_price: 109.99 }))).toBe(30);
  });

  it("is null when there is no sale", () => {
    expect(discountPercent(price())).toBeNull();
  });
});
