import { Link, Route, Routes } from "react-router-dom";
import { Catalog } from "@/pages/Catalog";
import { ProductDetail } from "@/pages/ProductDetail";

export default function App() {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center px-4 py-4 sm:px-6">
          <Link to="/" className="rounded font-semibold tracking-tight">
            Catalogue
          </Link>
        </div>
      </header>

      {/* One main landmark, so a screen reader can skip the header. */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <Routes>
          <Route path="/" element={<Catalog />} />
          <Route path="/product/:id" element={<ProductDetail />} />
        </Routes>
      </main>
    </div>
  );
}
