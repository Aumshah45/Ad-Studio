import { Lock } from "lucide-react";

import type { NormBoxView, ProductScaleView, SpecSummary } from "@/lib/api";
import type { PlanExtras } from "@/lib/run-events";
import { displayTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";

export type Box = NormBoxView;

const HEMISPHERE: Record<string, string> = {
  south: "southern hemisphere",
  north: "northern hemisphere",
  equatorial: "equatorial",
};
const ANCHOR_Y:Record<string, number> = { lower_center: 0.62, center: 0.5, upper_center: 0.38 };

/** Approximate product box from the spec's composition (anchor + scale), for the diagram only. */
export function productBoxFrom(anchor: string | null, scale: number | null): Box | null {
  if (!anchor || scale == null) return null;
  const cy = ANCHOR_Y[anchor] ?? 0.5;
  const half = scale / 2;
  return { x0: 0.5 - half, x1: 0.5 + half, y0: Math.max(0, cy - half), y1: Math.min(1, cy + half) };
}

const FRAMING: Record<string, string> = {
  close_up: "Close-up (tabletop, about 35–55 cm of scene height)",
  medium: "Medium shot (about 0.8–1.2 m of scene height)",
  wide: "Wide shot (about 2–3 m of scene height)",
};

export function framingLabel(framing: string): string {
  return FRAMING[framing] ?? framing.replaceAll("_", " ");
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** "18–30% of the image height" (the product's longest side). */
export function scaleRangeText(scale: Pick<ProductScaleView, "scale_min" | "scale_max">): string {
  return scale.scale_min === scale.scale_max
    ? `${pct(scale.scale_min)} of the image height`
    : `${Math.round(scale.scale_min * 100)}–${pct(scale.scale_max)} of the image height`;
}

/** "about 11 cm (small)". */
export function realSizeText(scale: Pick<ProductScaleView, "size_cm" | "size_class">): string {
  const cm = scale.size_cm >= 100 ? `${(scale.size_cm / 100).toFixed(1)} m` : `${Math.round(scale.size_cm)} cm`;
  return `about ${cm} at its largest (${scale.size_class.replaceAll("_", " ")})`;
}

export interface ScaleBand {
  anchor: string | null;
  min: number;
  max: number;
}

/** The ad canvas at its aspect ratio with the reserved text zone (and product area) drawn in. */
export function TextZoneDiagram({
  aspect,
  zone,
  productBox,
  bandColor,
  scaleBand,
  className,
}: {
  aspect: string;
  zone: Box;
  productBox?: Box | null;
  bandColor?: string | null;
  /** ADR-007: the product's planned size range (longest side / image height) at its anchor. */
  scaleBand?: ScaleBand | null;
  className?: string;
}) {
  const w = aspect === "1:1" ? 100 : 80;
  const h = 100;
  const rect = (b: Box) => ({ x: b.x0 * w, y: b.y0 * h, width: (b.x1 - b.x0) * w, height: (b.y1 - b.y0) * h });
  const z = rect(zone);
  // Scale is a fraction of the image height; on a 4:5 canvas the same length is a larger share of the width.
  const squareBox = (scale: number): Box | null => {
    const b = productBoxFrom(scaleBand?.anchor ?? "center", scale);
    if (!b) return null;
    const halfW = (scale * h) / w / 2;
    return { ...b, x0: Math.max(0, 0.5 - halfW), x1: Math.min(1, 0.5 + halfW) };
  };
  const outer = scaleBand ? squareBox(scaleBand.max) : null;
  const inner = scaleBand ? squareBox(scaleBand.min) : null;
  const o = outer ? rect(outer) : null;
  const i = inner ? rect(inner) : null;
  const p = !o && productBox ? rect(productBox) : null;
  return (
    <figure className={cn("space-y-1", className)}>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full rounded-md border bg-muted/60"
        role="img"
        aria-label={`${aspect} canvas; text zone covers ${Math.round((zone.y1 - zone.y0) * 100)}% of the height at the ${zone.y0 < 0.5 ? "top" : "bottom"}`}
      >
        {o && i ? (
          <g data-slot="scale-band">
            <rect {...o} rx={2} className="fill-emerald-600/15 stroke-emerald-700 dark:stroke-emerald-400" strokeWidth={0.6} />
            <rect {...i} rx={1.5} className="fill-background/70 stroke-emerald-700 dark:stroke-emerald-400" strokeWidth={0.5} strokeDasharray="1.5 1" />
            <text x={o.x + o.width / 2} y={o.y + 5} textAnchor="middle" className="fill-emerald-800 dark:fill-emerald-300" fontSize={5}>
              Product
            </text>
          </g>
        ) : null}
        {p ? (
          <g>
            <rect {...p} rx={2} className="fill-foreground/5 stroke-muted-foreground" strokeWidth={0.6} strokeDasharray="2 1.5" />
            <text x={p.x + p.width / 2} y={p.y + p.height / 2} textAnchor="middle" dominantBaseline="middle" className="fill-muted-foreground" fontSize={6}>
              Product
            </text>
          </g>
        ) : null}
        <rect {...z} style={bandColor ? { fill: bandColor, fillOpacity: 0.35 } : undefined} className={cn(!bandColor && "fill-primary/15")} />
        <rect {...z} fill="none" className="stroke-primary" strokeWidth={0.8} strokeDasharray="3 1.5" />
        <text x={z.x + z.width / 2} y={z.y + z.height / 2} textAnchor="middle" dominantBaseline="middle" className="fill-primary font-medium" fontSize={6}>
          Text zone
        </text>
      </svg>
      <figcaption className="text-center text-[11px] text-muted-foreground tabular-nums">
        {aspect} · long edge ≤ 1024 px
        {scaleBand ? (
          <span className="block">
            Product band {Math.round(scaleBand.min * 100)}–{pct(scaleBand.max)} of height
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}

function Chips({ items, mono = false }: { items: string[]; mono?: boolean }) {
  return (
    <ul className="flex flex-wrap gap-1">
      {items.map((it) => (
        <li key={it} className={cn("rounded-md bg-muted px-2 py-0.5 text-xs", mono && "font-mono")}>
          {it}
        </li>
      ))}
    </ul>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[120px_1fr] sm:gap-3">
      <dt className="text-xs text-muted-foreground sm:pt-0.5">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** ADR-007: framing, the product's real size, the planned scale range and the reference objects. */
export function ProductScaleRows({ scale }: { scale: ProductScaleView }) {
  return (
    <>
      <Row label="Framing">{framingLabel(scale.framing)}</Row>
      <Row label="Product size">{realSizeText(scale)}</Row>
      <Row label="Product scale">
        <span className="tabular-nums">{scaleRangeText(scale)}</span>
        {scale.resting_surface ? (
          <span className="text-muted-foreground"> · rests on {scale.resting_surface}</span>
        ) : null}
      </Row>
      {scale.scale_references?.length ? (
        <Row label="Sized next to">
          <Chips items={scale.scale_references} />
        </Row>
      ) : null}
    </>
  );
}

/** The resolved plan: effective season with its source, locale cues, palette, tokens and text zone. */
export function PlanSpecCard({
  plan,
  extras,
  aspect,
}: {
  plan: SpecSummary;
  extras: PlanExtras;
  aspect: string;
}) {
  const hemisphere = plan.hemisphere ? (HEMISPHERE[plan.hemisphere] ?? plan.hemisphere) : null;
  const zone = plan.text_zone ?? null;
  return (
    <div className="grid gap-4 rounded-lg border bg-card p-4 text-sm md:grid-cols-[minmax(0,1fr)_132px]">
      <div className="min-w-0 space-y-3">
        <div>
          <p className="text-base">
            Effective season: <strong className="font-semibold">{plan.effective_season ?? "unknown"}</strong>
            {plan.country_name || hemisphere ? (
              <span className="text-muted-foreground">
                {" "}
                ({[plan.country_name, hemisphere].filter(Boolean).join(", ")})
              </span>
            ) : null}
          </p>
          {plan.rationale ? <p className="mt-1 text-muted-foreground">{plan.rationale}</p> : null}
          <p className="mt-1 text-xs text-muted-foreground">
            Season from the hemisphere table (deterministic) · Scene from{" "}
            {plan.source === "planner" ? (
              <>
                the planner
                {plan.planner_model ? <span className="font-mono"> {plan.planner_model}</span> : null}
              </>
            ) : (
              "the default cue table (no planner call)"
            )}
          </p>
        </div>
        <dl className="space-y-2">
          {extras.setting ? <Row label="Setting">{extras.setting}</Row> : null}
          {plan.locale_cues?.length ? (
            <Row label="Locale cues">
              <Chips items={plan.locale_cues} />
            </Row>
          ) : null}
          {extras.palette.length ? (
            <Row label="Palette">
              <ul className="flex flex-wrap gap-1.5">
                {extras.palette.map((hex) => (
                  <li key={hex} className="inline-flex items-center gap-1 font-mono text-xs">
                    <span className="size-4 rounded-sm border" style={{ backgroundColor: hex }} aria-hidden />
                    {hex}
                  </li>
                ))}
              </ul>
            </Row>
          ) : null}
          {plan.text_lines?.length ? (
            <Row label="Required text">
              <p className="inline-flex items-start gap-1.5 font-mono text-xs whitespace-pre-line">
                <Lock className="mt-0.5 size-3 shrink-0 text-muted-foreground" aria-label="Locked" />
                {plan.text_lines.join("\n")}
              </p>
            </Row>
          ) : null}
          {extras.criticalTokens.length ? (
            <Row label="Must match exactly">
              <Chips items={displayTokens(extras.criticalTokens, plan.text_lines?.join("\n"))} mono />
            </Row>
          ) : null}
          <Row label="Text rendering">
            {plan.text_mode === "overlay_only"
              ? "Drawn by Ad Studio in the text zone (this script isn't rendered natively)"
              : "Native, with the Ad Studio text fallback if it fails"}
          </Row>
          {plan.product_scale ? <ProductScaleRows scale={plan.product_scale} /> : null}
          {plan.avoid?.length ? (
            <Row label="Avoid">
              <Chips items={plan.avoid} />
            </Row>
          ) : null}
        </dl>
      </div>
      {zone ? (
        <TextZoneDiagram
          aspect={extras.aspect ?? aspect}
          zone={zone}
          productBox={productBoxFrom(extras.productAnchor, extras.productScale)}
          bandColor={extras.bandColor}
          scaleBand={
            plan.product_scale
              ? { anchor: extras.productAnchor, min: plan.product_scale.scale_min, max: plan.product_scale.scale_max }
              : null
          }
          className="mx-auto w-28 md:w-full"
        />
      ) : null}
    </div>
  );
}
