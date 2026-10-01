/** "0.8 s", "38 s", "1 m 12 s"; em dash when unknown. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} m ${String(s % 60).padStart(2, "0")} s`;
}

/** "$0.12"; three decimals below one cent so tiny runs don't read as free. */
export function formatUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v > 0 && v < 0.01 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
}
