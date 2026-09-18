import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ProductImage } from "@/components/ProductImage";
import { discountPercent, formatPrice, isOnSale } from "@/lib/format";
import type { ProductSummary } from "@/types";

export function ProductCard({ product }: { product: ProductSummary }) {
  const onSale = isOnSale(product.price);
  const percent = discountPercent(product.price);

  return (
    // A real anchor, so keyboard nav and middle-click come free.
    <Link
      to={`/product/${product.id}`}
      className="group block rounded-lg"
      aria-label={product.name}
    >
      <Card className="h-full group-hover:shadow-md">
        <div className="bg-muted relative aspect-square overflow-hidden">
          <ProductImage
            src={product.image_url}
            alt={product.name}
            className="size-full transition-transform duration-300 group-hover:scale-105"
          />
          {onSale && (
            <Badge variant="sale" className="absolute top-2 left-2">
              {percent !== null ? `${percent}% off` : "Sale"}
            </Badge>
          )}
        </div>

        <CardContent className="space-y-1">
          <p className="text-muted-foreground text-xs tracking-wide uppercase">
            {product.brand}
          </p>
          {/* Names run to 70+ characters, so clamping keeps cards the same height. */}
          <h2 className="line-clamp-2 text-sm leading-snug font-medium">
            {product.name}
          </h2>
          <div className="flex flex-wrap items-baseline gap-2 pt-1">
            <span className="font-semibold">
              {formatPrice(product.price.price, product.price.currency)}
            </span>
            {onSale && product.price.compare_at_price !== null && (
              <span className="text-muted-foreground text-sm line-through">
                {formatPrice(product.price.compare_at_price, product.price.currency)}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
