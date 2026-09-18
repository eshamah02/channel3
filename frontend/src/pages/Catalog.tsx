import { ProductCard } from "@/components/ProductCard";
import { EmptyState, ErrorState, GridSkeleton } from "@/components/states";
import { listProducts, useAsync } from "@/lib/api";

export function Catalog() {
  const { data, loading, error, retry } = useAsync(listProducts);

  return (
    <>
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Catalogue</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {loading
              ? "Loading\u2026"
              : `${data?.length ?? 0} ${data?.length === 1 ? "product" : "products"}`}
          </p>
        </div>
      </div>

      {loading && <GridSkeleton />}
      {!loading && error && <ErrorState error={error} onRetry={retry} />}
      {!loading && !error && data?.length === 0 && <EmptyState />}
      {!loading && !error && data && data.length > 0 && (
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.map((product) => (
            <ProductCard key={product.id} product={product} />
          ))}
        </div>
      )}
    </>
  );
}
