"use client";

import { ArrowRight, FilterX, LayoutGrid, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { AdDetailSheet } from "@/components/batch/ad-detail-sheet";
import { AdTile } from "@/components/batch/ad-tile";
import { BATCH_COMMANDS, BatchRunDialog } from "@/components/batch/batch-run-dialog";
import { CodeText } from "@/components/code-text";
import { CommandLine, EmptyState } from "@/components/empty-state";
import { DryRunBadge } from "@/components/evals/primitives";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { EvalItem, EvalSummary, RunSummary } from "@/lib/api";
import {
  ALL_FILTER,
  SET_LABEL,
  buildAds,
  filterAds,
  filterOptions,
  loadGoldenRuns,
  type AdSet,
  type BatchAd,
  type FilterOptions,
  type GalleryFilter,
} from "@/lib/batch";
import { DIMENSION_LABEL } from "@/lib/checks";
import { EVAL_DIMENSIONS, fmtPct, loadItems, loadSummary, type GoldenVersion } from "@/lib/evals";
import { GoldenVersionSwitcher, syncVersionParam } from "@/components/evals/golden-version-switcher";
import { SOURCES_HREF } from "@/lib/sources";

type Option = { value: string; label: string };
type OptionGroup = { label: string; options: Option[] };

function FilterSelect({
  label,
  value,
  options,
  groups = [],
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: Option[];
  /** Extra labelled groups after the plain options. */
  groups?: OptionGroup[];
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const all = [...options, ...groups.flatMap((g) => g.options)];
  const items = Object.fromEntries(all.map((o) => [o.value, o.label]));
  return (
    <div className="min-w-0 space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <Select items={items} value={value} onValueChange={(v) => onChange((v as string | null) ?? "all")} disabled={disabled}>
        <SelectTrigger aria-label={label} className="w-full" data-filter={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
          {groups
            .filter((g) => g.options.length)
            .map((g) => (
              <SelectGroup key={g.label}>
                <SelectLabel>{g.label}</SelectLabel>
                {g.options.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** Verdict, dimension-failed, product and tag filters. */
export function GalleryFilters({
  value,
  onChange,
  options,
}: {
  value: GalleryFilter;
  onChange: (f: GalleryFilter) => void;
  options: FilterOptions;
}) {
  const { products, outcomeTags, briefTags } = options;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-slot="gallery-filters">
      <FilterSelect
        label="Verdict"
        value={value.verdict}
        onChange={(v) => onChange({ ...value, verdict: v as GalleryFilter["verdict"] })}
        options={[
          { value: "all", label: "All verdicts" },
          { value: "pass", label: "Pass" },
          { value: "fail", label: "Fail" },
          { value: "unverified", label: "Unsure" },
        ]}
      />
      <FilterSelect
        label="Dimension failed"
        value={value.dimension}
        onChange={(v) => onChange({ ...value, dimension: v as GalleryFilter["dimension"] })}
        options={[
          { value: "all", label: "Any dimension" },
          ...EVAL_DIMENSIONS.map((d) => ({ value: d, label: `${DIMENSION_LABEL[d]} failed` })),
        ]}
      />
      <FilterSelect
        label="Product"
        value={value.product}
        onChange={(v) => onChange({ ...value, product: v })}
        options={[
          { value: "all", label: "All products" },
          ...products.map((p) => ({ value: p.id, label: p.name ? `${p.id} · ${p.name}` : p.id })),
        ]}
      />
      <FilterSelect
        label="Tag"
        value={value.tag}
        onChange={(v) => onChange({ ...value, tag: v })}
        options={[{ value: "all", label: "All tags" }]}
        groups={[
          { label: "Outcome", options: outcomeTags.map((t) => ({ value: t, label: t })) },
          { label: "Brief", options: briefTags.map((t) => ({ value: t, label: t })) },
        ]}
        disabled={outcomeTags.length + briefTags.length === 0}
      />
    </div>
  );
}

/** First attempt → after repair, exact text and human agreement from the report. */
export function BatchSummaryBar({ summary }: { summary: EvalSummary }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" data-slot="batch-summary">
      <span className="text-muted-foreground">First attempt</span>
      <span className="font-semibold tabular-nums">{fmtPct(summary.first_attempt_pass_rate)}</span>
      <ArrowRight className="size-3.5 text-muted-foreground" aria-label="to" />
      <span className="text-muted-foreground">after repair</span>
      <span className="font-semibold text-primary tabular-nums">{fmtPct(summary.after_repair_pass_rate)}</span>
      <span className="text-muted-foreground">·</span>
      <span className="text-muted-foreground">exact text</span>
      <span className="font-semibold tabular-nums">{fmtPct(summary.text_exact_rate)}</span>
      <span className="text-muted-foreground">·</span>
      <span className="text-muted-foreground">human agreement</span>
      <span className="font-semibold tabular-nums">
        {summary.human_agreement_n ? fmtPct(summary.human_agreement) : "no labels yet"}
      </span>
    </p>
  );
}

/** Gallery keys (docs/ux.md): ←/→ previous/next tile, ↑/↓ the tile above/below in the grid. */
export function moveTileFocus(e: KeyboardEvent<HTMLUListElement>) {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
  const tiles = [...e.currentTarget.querySelectorAll<HTMLElement>("[data-ad]")];
  const i = tiles.indexOf(document.activeElement as HTMLElement);
  if (i < 0) return;
  let next: HTMLElement | undefined;
  if (e.key === "ArrowRight") next = tiles[i + 1];
  else if (e.key === "ArrowLeft") next = tiles[i - 1];
  else {
    const left = tiles[i]!.getBoundingClientRect().left;
    const sameColumn = (t: HTMLElement) => Math.abs(t.getBoundingClientRect().left - left) < 4;
    next =
      e.key === "ArrowDown"
        ? tiles.slice(i + 1).find(sameColumn)
        : tiles.slice(0, i).reverse().find(sameColumn);
  }
  if (next) {
    e.preventDefault();
    next.focus();
  }
}

type State =
  | { kind: "loading" }
  | { kind: "no-report" }
  | { kind: "error"; message: string }
  | { kind: "ok"; summary: EvalSummary; items: EvalItem[]; runs: RunSummary[] };

function asShown(summary: EvalSummary | null): GoldenVersion | null {
  const v = summary?.golden_version;
  return v === "v1" || v === "v2" ? v : null;
}

function syncUrl(id: string | null) {
  try {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("ad", id);
    else url.searchParams.delete("ad");
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // URL sync is a convenience only.
  }
}

/** The gallery itself, given loaded data (tested directly). */
export function BatchGalleryView({
  summary,
  items,
  runs,
  initialAd = null,
}: {
  summary: EvalSummary | null;
  items: EvalItem[];
  runs: RunSummary[];
  initialAd?: string | null;
}) {
  const ads = useMemo(() => buildAds(items, runs), [items, runs]);
  const initial = initialAd ? ads.find((a) => a.id === initialAd) : undefined;
  const [set, setSet] = useState<AdSet>(initial?.set ?? "final");
  const [filter, setFilter] = useState<GalleryFilter>(ALL_FILTER);
  const [openId, setOpenId] = useState<string | null>(initial?.id ?? null);
  const [sheetOpen, setSheetOpen] = useState(Boolean(initial));

  const shown = filterAds(ads, set, filter);
  const inSet = ads.filter((a) => a.set === set).length;
  const options = filterOptions(ads);
  const current: BatchAd | null = ads.find((a) => a.id === openId) ?? null;
  const sibling = current
    ? (ads.find((a) => a.briefId === current.briefId && a.set !== current.set) ?? null)
    : null;
  const filtered = JSON.stringify(filter) !== JSON.stringify(ALL_FILTER);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ToggleGroup
          variant="outline"
          spacing={0}
          value={[set]}
          onValueChange={(v: string[]) => {
            const next = v[0];
            if (next === "nat" || next === "final") setSet(next);
          }}
          aria-label="Image set"
        >
          <ToggleGroupItem value="final">{SET_LABEL.final}</ToggleGroupItem>
          <ToggleGroupItem value="nat">{SET_LABEL.nat}</ToggleGroupItem>
        </ToggleGroup>
        <span className="text-sm text-muted-foreground tabular-nums" aria-live="polite" data-slot="gallery-count">
          Showing {shown.length} of {inSet}
        </span>
      </div>
      <GalleryFilters value={filter} onChange={setFilter} options={options} />
      {shown.length ? (
        <ul
          className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
          aria-label="Golden ads"
          aria-describedby="gallery-keys"
          onKeyDown={moveTileFocus}
        >
          {shown.map((ad) => (
            <li key={ad.id}>
              <AdTile
                ad={ad}
                onOpen={(id) => {
                  setOpenId(id);
                  setSheetOpen(true);
                  syncUrl(id);
                }}
              />
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<FilterX aria-hidden />}
          title="No ads match these filters"
          body={filtered ? "Clear a filter or switch the image set." : "This set has no images in the report."}
        >
          {filtered ? (
            <Button variant="outline" onClick={() => setFilter(ALL_FILTER)}>
              Clear filters
            </Button>
          ) : null}
        </EmptyState>
      )}
      <p id="gallery-keys" className="sr-only">
        Arrow keys move between ads; Enter opens one; Escape closes it.
      </p>
      <AdDetailSheet
        ad={current}
        sibling={sibling}
        summary={summary}
        open={sheetOpen && current !== null}
        onOpenChange={(o) => {
          setSheetOpen(o);
          if (!o) syncUrl(null);
        }}
      />
    </div>
  );
}

/** `/batch`: the golden briefs' ads with verdicts, filters, detail sheet and the batch-run explainer. */
export function BatchGallery({
  initialAd = null,
  initialVersion = null,
}: {
  initialAd?: string | null;
  /** `?golden_version=` (v1 | v2); null = the newest report of any version. */
  initialVersion?: GoldenVersion | null;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [version, setVersion] = useState<GoldenVersion | null>(initialVersion);

  const load = useCallback(async () => {
    const [s, items, runs] = await Promise.all([
      loadSummary(version),
      loadItems("natural", version),
      loadGoldenRuns(),
    ]);
    if (s.kind === "no-report" || items.kind === "no-report") return setState({ kind: "no-report" });
    if (s.kind === "error") return setState({ kind: "error", message: s.message });
    if (items.kind === "error") return setState({ kind: "error", message: items.message });
    setState({ kind: "ok", summary: s.data, items: items.data, runs });
  }, [version]);

  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);

  const summary = state.kind === "ok" ? state.summary : null;
  const briefs = state.kind === "ok" ? new Set(state.items.map((i) => i.brief_id)).size : 20;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Batch</h1>
            {summary?.dry_run ? <DryRunBadge /> : null}
          </div>
          <p className="text-sm text-muted-foreground">
            {briefs} golden briefs across both hemispheres, run through the full pipeline.{" "}
            <Link
              href={SOURCES_HREF}
              className="rounded-sm text-primary underline-offset-2 outline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >
              Photo sources and licences
            </Link>
          </p>
          {summary ? <BatchSummaryBar summary={summary} /> : null}
        </div>
        <GoldenVersionSwitcher
          value={version ?? asShown(summary)}
          onChange={(v) => {
            setVersion(v);
            setState({ kind: "loading" });
            syncVersionParam(v);
          }}
        />
        <BatchRunDialog
          briefCount={briefs || 20}
          costPerAd={summary?.cost_per_approved_ad}
          p50Ms={summary?.p50_latency_to_approved_ms}
          estimateFromDryRun={summary?.dry_run}
        />
      </div>

      {state.kind === "loading" ? (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" aria-busy="true" aria-label="Loading ads">
          {Array.from({ length: 10 }, (_, i) => (
            <li key={i}>
              <Skeleton className="aspect-[4/6] w-full motion-reduce:animate-none" />
            </li>
          ))}
        </ul>
      ) : state.kind === "no-report" ? (
        <EmptyState
          icon={<LayoutGrid aria-hidden />}
          title={version ? `No ${version} batch outputs yet` : "No batch outputs yet"}
          body={
            version === "v2"
              ? "Golden v2 (realistic scale and natural integration, ADR-007) hasn't been run and evaluated yet. Run the 20 briefs, export them to v2, then evaluate and import."
              : "Run the 20 golden briefs to build the evaluation set, then import it for this page."
          }
        >
          <CommandLine command={BATCH_COMMANDS} />
          <p className="text-xs text-muted-foreground">
            No API key? <code className="font-mono">make golden-dryrun</code> runs the whole chain on fake clients.
          </p>
        </EmptyState>
      ) : state.kind === "error" ? (
        <div className="space-y-3">
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t load the batch</AlertTitle>
            <AlertDescription>
              <CodeText>{state.message}</CodeText>
            </AlertDescription>
          </Alert>
          <Button
            variant="outline"
            onClick={() => {
              setState({ kind: "loading" });
              void load();
            }}
          >
            <RotateCw aria-hidden /> Retry
          </Button>
        </div>
      ) : (
        <BatchGalleryView key={version ?? "newest"} summary={state.summary} items={state.items} runs={state.runs} initialAd={initialAd} />
      )}
    </div>
  );
}
