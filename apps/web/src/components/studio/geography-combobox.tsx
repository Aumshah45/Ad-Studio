"use client";

import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { COUNTRIES, type Country } from "@/lib/countries";

const HEMISPHERE_HINT: Record<NonNullable<Country["hemisphere"]>, string> = {
  north: "Northern hemisphere",
  south: "Southern hemisphere",
  equatorial: "Equatorial: no winter or summer, the planner uses wet and dry seasons",
};

export function hemisphereHint(country: Country | null): string | null {
  if (!country) return null;
  return country.hemisphere
    ? HEMISPHERE_HINT[country.hemisphere]
    : "No market data yet: the planner uses a conservative default";
}

export function countryByCode(code: string): Country | null {
  return COUNTRIES.find((c) => c.code === code.toUpperCase()) ?? null;
}

function matches(country: Country, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return country.name.toLowerCase().includes(q) || country.code.toLowerCase() === q;
}

interface GeographyComboboxProps {
  id?: string;
  value: Country | null;
  onChange: (country: Country | null) => void;
  invalid?: boolean;
  describedBy?: string;
  disabled?: boolean;
}

/** Searchable ISO 3166-1 country picker (name or 2-letter code). No flags. */
export function GeographyCombobox({ id, value, onChange, invalid, describedBy, disabled }: GeographyComboboxProps) {
  return (
    <Combobox<Country>
      items={COUNTRIES}
      value={value}
      onValueChange={(v) => onChange(v)}
      itemToStringLabel={(c) => c.name}
      itemToStringValue={(c) => c.code}
      isItemEqualToValue={(a, b) => a.code === b.code}
      filter={(item, query) => matches(item, query)}
      disabled={disabled}
    >
      <ComboboxInput
        id={id}
        className="w-full"
        placeholder="Search countries"
        triggerLabel="Show countries"
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        disabled={disabled}
      />
      <ComboboxContent>
        <ComboboxEmpty>No country matches. Try its English name or ISO code.</ComboboxEmpty>
        <ComboboxList>
          {(item: Country) => (
            <ComboboxItem key={item.code} value={item}>
              <span className="flex-1 truncate">{item.name}</span>
              <span className="font-mono text-xs text-muted-foreground">{item.code}</span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
