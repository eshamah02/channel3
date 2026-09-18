import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "sale";

const variants: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground",
  sale: "bg-sale text-white",
};

export function Badge({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"span"> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
