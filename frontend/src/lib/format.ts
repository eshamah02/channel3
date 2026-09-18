import type { Price } from "@/types";

/**
 * Format a price in its own currency. Never hardcode a symbol: the catalogue mixes
 * them, so "$" would misprice the GBP product. Falls back to "CODE 12.34" when Intl
 * rejects the code, which happens when the currency was inferred rather than declared.
 */
export function formatPrice(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

/** True when the page showed a higher former price. */
export function isOnSale(price: Price): boolean {
  return price.compare_at_price !== null && price.compare_at_price > price.price;
}

/** Percentage off, rounded, for the sale badge. */
export function discountPercent(price: Price): number | null {
  if (!isOnSale(price) || !price.compare_at_price) return null;
  return Math.round(
    ((price.compare_at_price - price.price) / price.compare_at_price) * 100,
  );
}
