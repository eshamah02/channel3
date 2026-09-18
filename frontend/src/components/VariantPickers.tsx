import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { isValueAvailable, type Selection } from "@/lib/variants";
import type { Variant, VariantOption } from "@/types";

export function VariantPickers({
  options,
  variants,
  selection,
  complete,
  onSelect,
}: {
  options: VariantOption[];
  variants: Variant[];
  selection: Selection;
  /** Whether the variant data describes real combinations. */
  complete: boolean;
  onSelect: (axis: string, value: string) => void;
}) {
  // The drill and the lamp have no axes. An empty control is worse than none.
  if (options.length === 0) return null;

  return (
    <div className="space-y-5">
      {options.map((option) => {
        const labelId = `axis-${option.name.replace(/\s+/g, "-").toLowerCase()}`;
        return (
          <div key={option.name} className="space-y-2">
            <div className="flex items-baseline justify-between gap-2">
              <span id={labelId} className="text-sm font-medium">
                {option.name}
              </span>
              <span className="text-muted-foreground text-sm">
                {selection[option.name]}
              </span>
            </div>

            <RadioGroup
              aria-labelledby={labelId}
              value={selection[option.name] ?? ""}
              onValueChange={(value) => onSelect(option.name, value)}
            >
              {option.values.map((value) => {
                // Only a complete matrix knows what is unavailable.
                const unavailable =
                  complete && !isValueAvailable(variants, selection, option.name, value);
                return (
                  <RadioGroupItem
                    key={value}
                    value={value}
                    disabled={unavailable}
                    aria-label={
                      unavailable ? `${value}, unavailable` : value
                    }
                  >
                    {value}
                  </RadioGroupItem>
                );
              })}
            </RadioGroup>
          </div>
        );
      })}

      {!complete && (
        // Said plainly rather than implied by controls that do nothing.
        <p className="text-muted-foreground text-xs">
          This page lists the available options but not which combinations are in
          stock.
        </p>
      )}
    </div>
  );
}
