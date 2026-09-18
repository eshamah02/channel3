import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductDetail } from "@/pages/ProductDetail";
import type { ExtractedProduct, Price, Variant } from "@/types";

const price = (over: Partial<Price> = {}): Price => ({
  price: 76.99,
  currency: "GBP",
  compare_at_price: null,
  ...over,
});

const combo = (pairs: Record<string, string>, over: Partial<Variant> = {}): Variant => ({
  option_values: Object.entries(pairs).map(([name, value]) => ({ name, value })),
  sku: null,
  price: null,
  in_stock: null,
  image_urls: [],
  ...over,
});

function envelope(over: Partial<ExtractedProduct> = {}): ExtractedProduct {
  return {
    id: "a3b5b70c60545e2e",
    slug: "shoe",
    source_url: "https://shop.test/p/shoe",
    source_file: "data/shoe.html",
    content_hash: "a".repeat(64),
    options: [],
    product: {
      name: "Court Sneaker",
      price: price(),
      description: "A leather sneaker.",
      key_features: ["Leather upper", "Rubber sole"],
      image_urls: ["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"],
      video_url: null,
      category: { name: "Apparel & Accessories > Shoes" },
      brand: "Sportswear Co",
      colors: [],
      variants: [],
    },
    extraction: { field_tiers: {}, escalated: false, warnings: [], cost_usd: 0.001 },
    ...over,
  };
}

/** Declared matrix: every variant names both axes, one combination missing. */
function withCompleteMatrix(): ExtractedProduct {
  const base = envelope();
  return {
    ...base,
    options: [
      { name: "Color", values: ["Black"] },
      { name: "Size", values: ["7", "8", "9"] },
    ],
    product: {
      ...base.product,
      variants: [
        combo({ Color: "Black", Size: "7" }, { price: price({ price: 70 }), in_stock: true }),
        combo(
          { Color: "Black", Size: "8" },
          {
            price: price({ price: 80 }),
            in_stock: false,
            image_urls: ["https://cdn.test/size8.jpg"],
          },
        ),
        // Size 9 is not offered.
      ],
    },
  };
}

/** Observed axes: each variant names one axis, no combination asserted. */
function withObservedAxes(): ExtractedProduct {
  const base = envelope();
  return {
    ...base,
    options: [
      { name: "Color", values: ["Black", "White"] },
      { name: "Size", values: ["S", "M"] },
    ],
    product: {
      ...base.product,
      variants: [
        combo({ Color: "Black" }),
        combo({ Color: "White" }),
        combo({ Size: "S" }),
        combo({ Size: "M" }),
      ],
    },
  };
}

function mockFetch(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => body } as Response);
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/product/a3b5b70c60545e2e"]}>
      <Routes>
        <Route path="/product/:id" element={<ProductDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProductDetail", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch(envelope()));
  });

  it("shows a skeleton while loading", async () => {
    renderDetail();
    expect(screen.getByLabelText("Loading product")).toBeInTheDocument();
    await screen.findByRole("heading", { level: 1, name: "Court Sneaker" });
  });

  it("renders the product's own details", async () => {
    renderDetail();

    expect(await screen.findByRole("heading", { level: 1, name: "Court Sneaker" })).toBeInTheDocument();
    expect(screen.getByText("Sportswear Co")).toBeInTheDocument();
    expect(screen.getByText("£76.99")).toBeInTheDocument();
    expect(screen.getByText("A leather sneaker.")).toBeInTheDocument();
    expect(screen.getByText("Apparel & Accessories > Shoes")).toBeInTheDocument();
  });

  it("renders key features as a real list", async () => {
    renderDetail();
    const items = await screen.findAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Leather upper", "Rubber sole"]);
  });

  it("links back to the catalogue and to the source page", async () => {
    renderDetail();
    expect(await screen.findByRole("link", { name: /back to catalogue/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /original page/i })).toHaveAttribute(
      "href",
      "https://shop.test/p/shoe",
    );
  });

  it("shows a 404 state for an unknown id", async () => {
    vi.stubGlobal("fetch", mockFetch(null, false, 404));
    renderDetail();
    expect(await screen.findByText("Product not found")).toBeInTheDocument();
  });

  it("offers a retry for a server error", async () => {
    vi.stubGlobal("fetch", mockFetch(null, false, 500));
    renderDetail();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  // --- pickers -------------------------------------------------------------

  it("renders no picker for a product with no axes", async () => {
    renderDetail();
    await screen.findByRole("heading", { level: 1 });
    // The drill and the lamp are like this; an empty control is worse than none.
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("renders one labelled radio group per axis", async () => {
    vi.stubGlobal("fetch", mockFetch(withCompleteMatrix()));
    renderDetail();

    const groups = await screen.findAllByRole("radiogroup");
    expect(groups).toHaveLength(2);
    expect(screen.getByRole("radiogroup", { name: "Color" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Size" })).toBeInTheDocument();
  });

  it("opens on a combination that exists", async () => {
    vi.stubGlobal("fetch", mockFetch(withCompleteMatrix()));
    renderDetail();
    expect(await screen.findByText("£70.00")).toBeInTheDocument();
  });

  it("updates price and stock when a size is selected", async () => {
    vi.stubGlobal("fetch", mockFetch(withCompleteMatrix()));
    renderDetail();

    expect(await screen.findByText("£70.00")).toBeInTheDocument();
    expect(screen.getByText("In stock")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: "8" }));

    expect(screen.getByText("£80.00")).toBeInTheDocument();
    expect(screen.getByText("Out of stock")).toBeInTheDocument();
  });

  it("swaps the gallery when the variant has its own images", async () => {
    vi.stubGlobal("fetch", mockFetch(withCompleteMatrix()));
    renderDetail();

    await screen.findByAltText("Court Sneaker — image 1 of 2");
    await userEvent.click(screen.getByRole("radio", { name: "8" }));

    expect(screen.getByAltText("Court Sneaker")).toBeInTheDocument();
  });

  it("disables a combination the matrix does not offer", async () => {
    vi.stubGlobal("fetch", mockFetch(withCompleteMatrix()));
    renderDetail();

    // Visible but struck through: hiding it reads as a bug.
    const missing = await screen.findByRole("radio", { name: "9, unavailable" });
    expect(missing).toBeDisabled();
    expect(screen.getByRole("radio", { name: "7" })).toBeEnabled();
  });

  it("disables nothing when the data is only axis observations", async () => {
    // The failure this guards against: applying the matching rule to single-axis
    // variants would disable every option on the page.
    vi.stubGlobal("fetch", mockFetch(withObservedAxes()));
    renderDetail();

    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(4);
    for (const radio of radios) expect(radio).toBeEnabled();
  });

  it("says so plainly when combinations were not published", async () => {
    vi.stubGlobal("fetch", mockFetch(withObservedAxes()));
    renderDetail();
    expect(
      await screen.findByText(/lists the available options but not which combinations/i),
    ).toBeInTheDocument();
  });

  it("keeps showing the product price for observed axes", async () => {
    vi.stubGlobal("fetch", mockFetch(withObservedAxes()));
    renderDetail();

    expect(await screen.findByText("£76.99")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "White" }));
    expect(screen.getByText("£76.99")).toBeInTheDocument();
  });

  // --- availability --------------------------------------------------------

  it("reports unknown availability rather than claiming stock", async () => {
    renderDetail();
    // Every product in the sample catalogue is in this state.
    expect(await screen.findByText("Availability not published")).toBeInTheDocument();
  });

  // --- gallery -------------------------------------------------------------

  it("numbers the primary image and offers thumbnails", async () => {
    renderDetail();
    expect(await screen.findByAltText("Court Sneaker — image 1 of 2")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Product images" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Show image/ })).toHaveLength(2);
  });

  it("changes the primary image when a thumbnail is chosen", async () => {
    renderDetail();
    await screen.findByAltText("Court Sneaker — image 1 of 2");

    await userEvent.click(screen.getByRole("button", { name: "Show image 2 of 2" }));
    expect(screen.getByAltText("Court Sneaker — image 2 of 2")).toBeInTheDocument();
  });

  it("navigates the gallery with arrow keys", async () => {
    renderDetail();
    await screen.findByAltText("Court Sneaker — image 1 of 2");

    screen.getByRole("button", { name: "Show image 1 of 2" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByAltText("Court Sneaker — image 2 of 2")).toBeInTheDocument();

    // Wraps around rather than dead-ending.
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByAltText("Court Sneaker — image 1 of 2")).toBeInTheDocument();
  });

  it("omits the thumbnail strip for a single image", async () => {
    const one = envelope();
    one.product.image_urls = ["https://cdn.test/only.jpg"];
    vi.stubGlobal("fetch", mockFetch(one));
    renderDetail();

    expect(await screen.findByAltText("Court Sneaker")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Product images" })).not.toBeInTheDocument();
  });

  it("shows a placeholder when a product has no images", async () => {
    const none = envelope();
    none.product.image_urls = [];
    vi.stubGlobal("fetch", mockFetch(none));
    renderDetail();

    expect(
      await screen.findByLabelText("No image available for Court Sneaker"),
    ).toBeInTheDocument();
  });

  // --- sale ----------------------------------------------------------------

  it("shows a struck-through former price and a badge", async () => {
    const sale = envelope();
    sale.product.price = price({ price: 76.99, compare_at_price: 109.99 });
    vi.stubGlobal("fetch", mockFetch(sale));
    renderDetail();

    expect(await screen.findByText("£109.99")).toBeInTheDocument();
    expect(screen.getByText("30% off")).toBeInTheDocument();
  });

  it("has exactly one h1", async () => {
    renderDetail();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
