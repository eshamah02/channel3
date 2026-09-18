import * as React from "react";
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { cn } from "@/lib/utils";

/**
 * Radix radio group, styled as selectable pills. A real radio group rather than a
 * row of divs, so arrow keys, tab order and screen-reader announcements come free.
 */

export function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      className={cn("flex flex-wrap gap-2", className)}
      {...props}
    />
  );
}

export function RadioGroupItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      className={cn(
        "min-w-11 cursor-pointer rounded-md border px-3 py-2 text-sm transition-colors",
        "hover:border-foreground/40",
        "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
        // Unavailable options stay visible but struck through: hiding them reads
        // as a bug, greying them reads as "not this one".
        "disabled:text-muted-foreground disabled:cursor-not-allowed disabled:line-through disabled:opacity-60 disabled:hover:border-border",
        className,
      )}
      {...props}
    >
      {children}
    </RadioGroupPrimitive.Item>
  );
}
