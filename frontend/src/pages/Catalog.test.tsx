import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Catalog } from "@/pages/Catalog";
import type { ProductSummary } from "@/types";

const summary = (over: Partial<ProductSummary> = {}): ProductSummary => ({
  id: "a3b5b70c60545e2e",
  slug: "nike-air-force-1",
  name: "Air Force 1 '07 LV8 Men's Shoes",
  brand: "Sportswear Co",
  price: { price: 76.99, currency: "GBP", compare_at_price: 109.99 },
  image_url: "https://cdn.test/shoe.jpg",
  ...over,
});

function mockFetch(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as Response);
}

function renderCatalog() {
  return render(
    <MemoryRouter>
      <Catalog />
    </MemoryRouter>,
  );
}

describe("Catalog", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch([]));
  });

  it("shows a loading state before the data arrives", async () => {
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();

    expect(screen.getByLabelText("Loading products")).toBeInTheDocument();

    // Wait for the request to settle inside the test, so the state update it
    // causes is not attributed to a torn-down component.
    await screen.findByText("Air Force 1 '07 LV8 Men's Shoes");
    expect(screen.queryByLabelText("Loading products")).not.toBeInTheDocument();
  });

  it("renders a card per product", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch([summary(), summary({ id: "0000000000000001", name: "Second Thing" })]),
    );
    renderCatalog();

    expect(await screen.findByText("Air Force 1 '07 LV8 Men's Shoes")).toBeInTheDocument();
    expect(screen.getByText("Second Thing")).toBeInTheDocument();
    expect(screen.getByText("2 products")).toBeInTheDocument();
  });

  it("links each card to its detail route", async () => {
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();

    const link = await screen.findByRole("link", {
      name: "Air Force 1 '07 LV8 Men's Shoes",
    });
    expect(link).toHaveAttribute("href", "/product/a3b5b70c60545e2e");
  });

  it("formats the price in the product's own currency", async () => {
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();
    expect(await screen.findByText("£76.99")).toBeInTheDocument();
  });

  it("shows a struck-through former price and a sale badge", async () => {
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();

    expect(await screen.findByText("£109.99")).toBeInTheDocument();
    expect(screen.getByText("30% off")).toBeInTheDocument();
  });

  it("shows no sale badge when there is no former price", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch([summary({ price: { price: 129, currency: "USD", compare_at_price: null } })]),
    );
    renderCatalog();

    expect(await screen.findByText("$129.00")).toBeInTheDocument();
    expect(screen.queryByText(/off$/)).not.toBeInTheDocument();
  });

  it("names the ingest command when the catalogue is empty", async () => {
    renderCatalog();
    expect(await screen.findByText("No products yet")).toBeInTheDocument();
    expect(screen.getByText("uv run python main.py")).toBeInTheDocument();
  });

  it("offers a retry when the request fails", async () => {
    const failing = vi.fn().mockRejectedValue(new Error("Network down"));
    vi.stubGlobal("fetch", failing);
    renderCatalog();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Network down")).toBeInTheDocument();

    vi.stubGlobal("fetch", mockFetch([summary()]));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Air Force 1 '07 LV8 Men's Shoes")).toBeInTheDocument();
  });

  it("reports a failed response status", async () => {
    vi.stubGlobal("fetch", mockFetch(null, false, 500));
    renderCatalog();
    expect(await screen.findByText("Request failed (500)")).toBeInTheDocument();
  });

  it("falls back when an image cannot load", async () => {
    // 6 of 41 harvested URLs in the sample catalogue are unreachable.
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();

    const image = await screen.findByAltText("Air Force 1 '07 LV8 Men's Shoes");
    // fireEvent wraps the state update in act(), which dispatchEvent does not.
    fireEvent.error(image);

    expect(
      screen.getByLabelText("No image available for Air Force 1 '07 LV8 Men's Shoes"),
    ).toBeInTheDocument();
  });

  it("uses a placeholder when there is no image at all", async () => {
    vi.stubGlobal("fetch", mockFetch([summary({ image_url: null })]));
    renderCatalog();
    expect(
      await screen.findByLabelText("No image available for Air Force 1 '07 LV8 Men's Shoes"),
    ).toBeInTheDocument();
  });

  it("has exactly one h1", async () => {
    vi.stubGlobal("fetch", mockFetch([summary()]));
    renderCatalog();
    await screen.findByText("Air Force 1 '07 LV8 Men's Shoes");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
