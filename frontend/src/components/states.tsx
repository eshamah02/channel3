import { AlertCircle, PackageOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Loading, empty and error: the states that decide whether a screen feels finished. */

export function GridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div
      className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      aria-busy="true"
      aria-live="polite"
      aria-label="Loading products"
    >
      {Array.from({ length: count }, (_, index) => (
        // Mirrors the real card's layout so the page does not jump when the
        // data arrives.
        <Card key={index}>
          <Skeleton className="aspect-square rounded-none" />
          <CardContent className="space-y-2">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-5 w-20" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed py-20 text-center">
      <PackageOpen className="text-muted-foreground size-8" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">No products yet</p>
        <p className="text-muted-foreground text-sm">
          Extract some pages, then refresh this page.
        </p>
      </div>
      {/* Naming the exact command turns a dead end into the next step. */}
      <code className="bg-muted rounded px-3 py-1.5 font-mono text-sm">
        uv run python main.py
      </code>
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: Error;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-4 rounded-lg border border-dashed py-20 text-center"
    >
      <AlertCircle className="text-destructive size-8" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">Could not load products</p>
        <p className="text-muted-foreground text-sm">{error.message}</p>
        <p className="text-muted-foreground text-sm">
          Is the API running on port 8000?
        </p>
      </div>
      <Button onClick={onRetry} variant="outline">
        Try again
      </Button>
    </div>
  );
}
