import { useEffect, useRef, useState } from "react";
import { ProductImage } from "@/components/ProductImage";
import { cn } from "@/lib/utils";

/**
 * Primary image plus a thumbnail strip. The thumbnails are buttons in a labelled
 * group with arrow-key navigation, and aria-current marks the shown image rather
 * than relying on border colour alone.
 */
export function Gallery({ images, name }: { images: string[]; name: string }) {
  const [index, setIndex] = useState(0);
  const strip = useRef<HTMLDivElement>(null);

  // A variant can swap the image set, making an index into the old one meaningless.
  useEffect(() => setIndex(0), [images]);

  const count = images.length;
  const current = images[Math.min(index, Math.max(count - 1, 0))] ?? null;

  function move(delta: number) {
    if (count === 0) return;
    const next = (index + delta + count) % count;
    setIndex(next);
    // Keep focus with the selection, which is what makes arrow keys feel right.
    const buttons = strip.current?.querySelectorAll<HTMLButtonElement>("button");
    buttons?.[next]?.focus();
  }

  return (
    <div className="space-y-3">
      <div className="bg-muted aspect-square overflow-hidden rounded-lg border">
        <ProductImage
          src={current}
          alt={count > 1 ? `${name} \u2014 image ${index + 1} of ${count}` : name}
          className="size-full"
        />
      </div>

      {count > 1 && (
        <div
          ref={strip}
          role="group"
          aria-label="Product images"
          className="grid grid-cols-5 gap-2 sm:grid-cols-6"
          onKeyDown={(event) => {
            if (event.key === "ArrowRight") {
              event.preventDefault();
              move(1);
            } else if (event.key === "ArrowLeft") {
              event.preventDefault();
              move(-1);
            }
          }}
        >
          {images.map((url, position) => (
            <button
              key={url}
              type="button"
              aria-label={`Show image ${position + 1} of ${count}`}
              aria-current={position === index}
              onClick={() => setIndex(position)}
              className={cn(
                "bg-muted aspect-square cursor-pointer overflow-hidden rounded-md border transition-opacity",
                position === index
                  ? "border-primary"
                  : "opacity-70 hover:opacity-100",
              )}
            >
              <ProductImage src={url} alt="" className="size-full" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
