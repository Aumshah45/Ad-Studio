"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { GOLDEN_VERSIONS, type GoldenVersion } from "@/lib/evals";

const VERSION_HINT: Record<GoldenVersion, string> = {
  v1: "baseline",
  v2: "realistic scale",
};

/** Sets or clears `?golden_version=` without a navigation (a shareable link, not a reload). */
export function syncVersionParam(version: GoldenVersion | null) {
  try {
    const url = new URL(window.location.href);
    if (version) url.searchParams.set("golden_version", version);
    else url.searchParams.delete("golden_version");
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // URL sync is a convenience only.
  }
}

/** v1 / v2 golden dataset switcher (ADR-007); `value` null = neither shown yet. */
export function GoldenVersionSwitcher({
  value,
  onChange,
  className,
}: {
  value: GoldenVersion | null;
  onChange: (v: GoldenVersion) => void;
  className?: string;
}) {
  return (
    <div className={className} data-slot="golden-version-switcher">
      <div className="mb-1 text-xs text-muted-foreground" id="golden-version-label">
        Golden version
      </div>
      <ToggleGroup
        variant="outline"
        spacing={0}
        value={value ? [value] : []}
        onValueChange={(v: string[]) => {
          const next = v[0];
          if (next === "v1" || next === "v2") onChange(next);
        }}
        aria-labelledby="golden-version-label"
      >
        {GOLDEN_VERSIONS.map((v) => (
          <ToggleGroupItem key={v} value={v} aria-label={`Golden ${v} (${VERSION_HINT[v]})`}>
            <span className="font-mono">{v}</span>
            <span className="hidden text-xs text-muted-foreground sm:inline">{VERSION_HINT[v]}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}
