import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, PackageX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Gallery } from "@/components/Gallery";
import { VariantPickers } from "@/components/VariantPickers";
import { ErrorState } from "@/components/states";
import { ApiError, getProduct, useAsync } from "@/lib/api";
import { discountPercent, formatPrice, isOnSale } from "@/lib/format";
import {
  initialSelection,
  isCombinationComplete,
  resolve,
  type Selection,
} from "@/lib/variants";
import type { ExtractedProduct } from "@/types";

export function ProductDetail() {
  const { id = "" } = useParams();
  const { data, loading, error, retry } = useAsync(() => getProduct(id), [id]);

  if (loading) return <DetailSkeleton />;
  if (error instanceof ApiError && error.status === 404) return <NotFound />;
  if (error) return <ErrorState error={error} onRetry={retry} />;
  if (!data) return null;

  return <Detail envelope={data} />;
}

function Detail({ envelope }: { envelope: ExtractedProduct }) {
  const { product, options } = envelope;

  const complete = useMemo(
    () => isCombinationComplete(options, product.variants),
    [options, product.variants],
  );
  const [selection, setSelection] = useState<Selection>(() =>
    initialSelection(options, product.variants),
  );

  const { price, images, inStock } = resolve(
    product,
    product.variants,
    selection,
    complete,
  );
  const onSale = isOnSale(price);
  const percent = discountPercent(price);

  return (
    <>
      <Button asChild variant="ghost" size="sm" className="-ml-3 mb-6">
        <Link to="/">
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back to catalogue
        </Link>
      </Button>

      <div className="grid gap-8 lg:grid-cols-2 lg:gap-12">
        <Gallery images={images} name={product.name} />

        {/* Sticky on tall viewports so the pickers stay reachable while the
            description scrolls. */}
        <div className="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <div className="space-y-2">
            <p className="text-muted-foreground text-sm tracking-wide uppercase">
              {product.brand}
            </p>
            <h1 className="text-2xl font-semibold tracking-tight">{product.name}</h1>
            <p className="text-muted-foreground text-xs">{product.category.name}</p>
          </div>

          <div className="flex flex-wrap items-baseline gap-3">
            <span className="text-2xl font-semibold">
              {formatPrice(price.price, price.currency)}
            </span>
            {onSale && price.compare_at_price !== null && (
              <>
                <span className="text-muted-foreground text-lg line-through">
                  {formatPrice(price.compare_at_price, price.currency)}
                </span>
                <Badge variant="sale">
                  {percent !== null ? `${percent}% off` : "Sale"}
                </Badge>
              </>
            )}
          </div>

          <Availability inStock={inStock} />

          <VariantPickers
            options={options}
            variants={product.variants}
            selection={selection}
            complete={complete}
            onSelect={(axis, value) =>
              setSelection((current) => ({ ...current, [axis]: value }))
            }
          />

          {product.colors.length > 0 && options.length === 0 && (
            // Only worth showing when it is not already a picker.
            <p className="text-sm">
              <span className="font-medium">Colours: </span>
              <span className="text-muted-foreground">{product.colors.join(", ")}</span>
            </p>
          )}

          {product.description && (
            <div className="space-y-2 border-t pt-6">
              <h2 className="text-sm font-medium">Description</h2>
              <p className="text-muted-foreground text-sm leading-relaxed">
                {product.description}
              </p>
            </div>
          )}

          {product.key_features.length > 0 && (
            <div className="space-y-2 border-t pt-6">
              <h2 className="text-sm font-medium">Details</h2>
              <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
                {product.key_features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
            </div>
          )}

          {product.video_url && (
            <div className="space-y-2 border-t pt-6">
              <h2 className="text-sm font-medium">Video</h2>
              <video controls preload="none" className="w-full rounded-lg border">
                <source src={product.video_url} />
                <a href={product.video_url}>Watch the video</a>
              </video>
            </div>
          )}

          {envelope.source_url && (
            <p className="border-t pt-6 text-xs">
              <a
                href={envelope.source_url}
                className="text-muted-foreground rounded underline underline-offset-2"
                target="_blank"
                rel="noreferrer noopener"
              >
                View the original page
              </a>
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function Availability({ inStock }: { inStock: boolean | null }) {
  // Three states, not two: no page here publishes per-variant availability, so
  // claiming "In stock" would be inventing it.
  if (inStock === null) {
    return (
      <p className="text-muted-foreground text-sm">Availability not published</p>
    );
  }
  return (
    <p className={inStock ? "text-sm" : "text-muted-foreground text-sm"}>
      {inStock ? "In stock" : "Out of stock"}
    </p>
  );
}

function NotFound() {
  return (
    <div className="flex flex-col items-center gap-4 py-20 text-center">
      <PackageX className="text-muted-foreground size-8" aria-hidden="true" />
      <div className="space-y-1">
        <h1 className="font-medium">Product not found</h1>
        <p className="text-muted-foreground text-sm">
          It may not have been extracted yet.
        </p>
      </div>
      <Button asChild variant="outline">
        <Link to="/">Back to catalogue</Link>
      </Button>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading product">
      <Skeleton className="mb-6 h-8 w-36" />
      <div className="grid gap-8 lg:grid-cols-2 lg:gap-12">
        <div className="space-y-3">
          <Skeleton className="aspect-square rounded-lg" />
          <div className="grid grid-cols-5 gap-2 sm:grid-cols-6">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="aspect-square rounded-md" />
            ))}
          </div>
        </div>
        <div className="space-y-6">
          <div className="space-y-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-7 w-3/4" />
            <Skeleton className="h-3 w-40" />
          </div>
          <Skeleton className="h-8 w-28" />
          <Skeleton className="h-4 w-32" />
          <div className="space-y-2">
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-10 w-full" />
          </div>
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </div>
  );
}
