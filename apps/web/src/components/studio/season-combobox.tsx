"use client";

import { Autocomplete } from "@base-ui/react/autocomplete";

import {
  ComboboxCollection,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxLabel,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { SEASON_GROUPS, SEASON_MAX_CHARS, type SeasonSuggestion } from "@/lib/seasons";

interface SeasonComboboxProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  invalid?: boolean;
  describedBy?: string;
  disabled?: boolean;
}

/**
 * Free-text season, month or holiday with suggestions. Any text is allowed; the API resolves it
 * against the hemisphere (422 `unknown-season` when it can't).
 */
export function SeasonCombobox({ id, value, onChange, invalid, describedBy, disabled }: SeasonComboboxProps) {
  return (
    <Autocomplete.Root
      items={SEASON_GROUPS}
      value={value}
      onValueChange={(v) => onChange(v.slice(0, SEASON_MAX_CHARS))}
      itemToStringValue={(s: SeasonSuggestion) => s.label}
      openOnInputClick
      disabled={disabled}
    >
      <Autocomplete.Input
        render={
          <Input
            id={id}
            placeholder="e.g. December, Summer, Diwali"
            maxLength={SEASON_MAX_CHARS}
            aria-invalid={invalid || undefined}
            aria-describedby={describedBy}
            autoComplete="off"
          />
        }
      />
      <ComboboxContent>
        <ComboboxEmpty>No suggestion. Use a month, a season or a holiday.</ComboboxEmpty>
        <ComboboxList>
          {(group: (typeof SEASON_GROUPS)[number]) => (
            <ComboboxGroup key={group.value} items={group.items}>
              <ComboboxLabel>{group.value}</ComboboxLabel>
              <ComboboxCollection>
                {(item: SeasonSuggestion) => (
                  <Autocomplete.Item
                    key={item.label}
                    value={item}
                    className="flex cursor-default items-center rounded-md px-1.5 py-1 text-sm outline-hidden select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                  >
                    {item.label}
                  </Autocomplete.Item>
                )}
              </ComboboxCollection>
            </ComboboxGroup>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Autocomplete.Root>
  );
}
