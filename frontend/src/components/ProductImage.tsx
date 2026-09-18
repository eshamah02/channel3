import { useState } from "react";
import { ImageOff } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A product image that degrades instead of breaking. The URLs point at live CDNs
 * and some are dead or access-controlled, so a failure becomes a placeholder.
 */
export function ProductImage({
  src,
  alt,
  className,
}: {
  src: string | null;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    return (
      <div
        className={cn(
          "bg-muted text-muted-foreground flex flex-col items-center justify-center gap-2 p-4",
          className,
        )}
        role="img"
        aria-label={`No image available for ${alt}`}
      >
        <ImageOff className="size-6" aria-hidden="true" />
        <span className="text-center text-xs">Image unavailable</span>
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={cn("size-full object-cover", className)}
    />
  );
}
