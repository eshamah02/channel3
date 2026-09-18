import { useCallback, useEffect, useState } from "react";
import type { ExtractedProduct, ProductSummary } from "@/types";

/** Requests go to /api, which the dev server proxies to the Python API. */
const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    // 404 is meaningful to the caller, so the status travels with the message.
    throw new ApiError(
      response.status === 404
        ? "Not found"
        : `Request failed (${response.status})`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function listProducts() {
  return get<ProductSummary[]>("/products");
}

export function getProduct(id: string) {
  return get<ExtractedProduct>(`/products/${id}`);
}

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  retry: () => void;
}

/**
 * Minimal data-fetching hook. Two endpoints do not justify a query library; they
 * need the three states rendered honestly and a way to try again.
 */
export function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    fetcher()
      .then((result) => {
        // Guard against a resolved request from a superseded render.
        if (active) setData(result);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause : new Error(String(cause)));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  return { data, loading, error, retry };
}
