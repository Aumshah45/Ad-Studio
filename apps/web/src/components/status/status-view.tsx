"use client";

import { RotateCw, ServerCrash } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { CodeText } from "@/components/code-text";
import { EmptyState } from "@/components/empty-state";
import { VerdictBadge } from "@/components/run/verdict-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  healthReady,
  opsSummary,
  type AdStudioStatus,
  type ModelRow,
  type OpsSummary,
  type ProviderStatus,
  type ReadyResponse,
} from "@/lib/api";
import { DIMENSION_LABEL, DIMENSION_ORDER, NOT_CHECKED, dimensionTitle } from "@/lib/checks";

const POLL_MS = 10_000;

/**
 * Per-dimension pass rate of recent runs' candidate evaluations (`runs.dimension_pass_rates`).
 * Composition is left out of older evaluations, so it reads "Not checked" until one has it.
 */
export function DimensionPassRates({ rates }: { rates: Record<string, number | null> | null | undefined }) {
  if (!rates || Object.keys(rates).length === 0) return null;
  return (
    <div className="space-y-2" data-slot="dimension-pass-rates">
      <h2 className="text-sm font-medium">Candidate pass rate per dimension (recent runs)</h2>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {DIMENSION_ORDER.map((d) => {
          const v = rates[d];
          return (
            <li key={d} className="rounded-lg border p-3" data-dimension={d} title={dimensionTitle(d)}>
              <div className="text-xs text-muted-foreground">{DIMENSION_LABEL[d]}</div>
              <div className={v == null ? "text-sm text-muted-foreground" : "text-xl font-semibold tabular-nums"}>
                {v == null ? NOT_CHECKED : pct(v)}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function ms(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v)} ms`;
}

/** Health words for VerdictBadge: an icon and a word, never colour alone. */
const HEALTH: Record<string, { status: string; label: string }> = {
  ok: { status: "pass", label: "OK" },
  degraded: { status: "degraded", label: "Degraded" },
  unavailable: { status: "fail", label: "Unavailable" },
};

export function HealthBadge({ status, className }: { status: string; className?: string }) {
  const h = HEALTH[status] ?? { status: "pending", label: status };
  return <VerdictBadge status={h.status} label={h.label} className={className} />;
}

function BreakerBadge({ breaker }: { breaker: string | undefined }) {
  if (!breaker || breaker === "closed") return <VerdictBadge status="pass" label="breaker closed" />;
  return <VerdictBadge status={breaker === "open" ? "fail" : "degraded"} label={`breaker ${breaker.replace(/_/g, " ")}`} />;
}

export const MODEL_ROLE: Record<string, string> = {
  image_candidate: "Image model · candidates",
  image_repair: "Image model · repairs",
  vision_judge: "Vision judge",
  chain: "Text model chain",
  judge: "Text judge",
};

function Stat({ title, value, hint }: { title: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-xl break-words tabular-nums sm:text-2xl">{value}</CardTitle>
      </CardHeader>
      {hint ? <CardContent className="text-xs break-words text-muted-foreground">{hint}</CardContent> : null}
    </Card>
  );
}

function Row({ children, ...rest }: { children: ReactNode } & Record<`data-${string}`, string>) {
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between" {...rest}>
      {children}
    </li>
  );
}

/** Image models, vision judge and OCR from `/ready`'s `adstudio` block. */
export function AdStudioCard({ adstudio }: { adstudio: AdStudioStatus | null | undefined }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Ad Studio models
          {adstudio ? <HealthBadge status={adstudio.status} /> : null}
        </CardTitle>
        <CardDescription>
          {adstudio?.detail ??
            "Image generation, the vision judge and OCR. When the vision judge is down, runs are held for review, never judged by a text model."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!adstudio ? (
          <p className="text-sm text-muted-foreground">This API doesn&apos;t report Ad Studio models.</p>
        ) : (
          <ul className="divide-y" aria-label="Ad Studio models">
            {adstudio.models.map((m: ModelRow) => (
              <Row key={m.role} data-role={m.role}>
                <div className="min-w-0 space-y-0.5">
                  <div className="text-sm font-medium">{MODEL_ROLE[m.role] ?? m.role}</div>
                  <div className="font-mono text-xs break-all text-muted-foreground">
                    {m.spec}
                    {m.recorded_as && m.recorded_as !== m.spec ? ` · recorded as ${m.recorded_as}` : ""}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <VerdictBadge
                    status={m.client === "fake" ? "info" : "neutral"}
                    label={m.client === "fake" ? "fake client" : `${m.client} client`}
                  />
                  <VerdictBadge status={m.configured ? "pass" : "fail"} label={m.configured ? "configured" : "no key"} />
                  <BreakerBadge breaker={m.breaker} />
                </div>
              </Row>
            ))}
            <Row data-role="ocr">
              <div className="min-w-0 space-y-0.5">
                <div className="text-sm font-medium">OCR</div>
                <div className="font-mono text-xs break-all text-muted-foreground">
                  {adstudio.ocr.engine} {adstudio.ocr.version} · {adstudio.ocr.languages} language packs
                </div>
                <ul className="flex flex-wrap gap-x-3 gap-y-0.5 pt-0.5 text-xs" aria-label="OCR scripts">
                  {Object.entries(adstudio.ocr.scripts).map(([script, ok]) => (
                    <li key={script} className={ok ? "" : "text-muted-foreground"}>
                      <span className="font-mono">{script}</span> {ok ? "✓ readable" : "✗ overlay only"}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <VerdictBadge
                  status={adstudio.ocr.available ? "pass" : "fail"}
                  label={adstudio.ocr.available ? "available" : "unavailable"}
                />
                <VerdictBadge
                  status={adstudio.ocr.shaping ? "pass" : "degraded"}
                  label={adstudio.ocr.shaping ? "complex-script shaping" : "no complex-script shaping"}
                />
              </div>
            </Row>
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ProvidersCard({ providers, detail }: { providers: ProviderStatus[]; detail: string | null | undefined }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Provider keys and breakers</CardTitle>
        <CardDescription>{detail ?? "Configured from .env"}</CardDescription>
      </CardHeader>
      <CardContent>
        {providers.length ? (
          <ul className="divide-y" aria-label="Provider keys and breakers">
            {providers.map((p) => (
              <Row key={`${p.role}-${p.spec}`} data-role={p.role}>
                <div className="min-w-0 space-y-0.5">
                  <div className="text-sm font-medium">{MODEL_ROLE[p.role] ?? p.role}</div>
                  <div className="font-mono text-xs break-all text-muted-foreground">{p.spec}</div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <VerdictBadge status={p.configured ? "pass" : "pending"} label={p.configured ? "configured" : "no key"} />
                  <BreakerBadge breaker={p.breaker} />
                </div>
              </Row>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No providers configured.</p>
        )}
      </CardContent>
    </Card>
  );
}

/** `/status`: API/DB, call-level ops, Ad Studio models and text providers; polls every 10 s. */
export function StatusView() {
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [ops, setOps] = useState<OpsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const poll = useCallback(async () => {
    try {
      const [r, o] = await Promise.all([healthReady(), opsSummary({ query: { window: 200 } })]);
      setReady(r.data ?? (r.error as ReadyResponse | undefined) ?? null);
      setOps(o.data ?? null);
      setError(r.data || r.error ? null : "The API did not respond.");
    } catch {
      setError("Can't reach Ad Studio's API. Is `make dev` running?");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    // Initial fetch, then poll; state updates happen after the await in poll().
    const first = setTimeout(poll, 0);
    const id = setInterval(poll, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [poll]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Status</h1>
        {ready ? <HealthBadge status={ready.status} className="h-7 text-sm" /> : null}
        <span className="ml-auto text-xs text-muted-foreground">Refreshes every 10 s</span>
      </div>
      {error && ready ? (
        <Alert variant="destructive">
          <AlertTitle>Lost contact with the API</AlertTitle>
          <AlertDescription>
            <CodeText>{error}</CodeText> Showing the last reading.
          </AlertDescription>
        </Alert>
      ) : null}
      {!loaded ? (
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4" aria-busy="true" aria-label="Loading status">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-28 motion-reduce:animate-none" />
          ))}
        </div>
      ) : error && !ready ? (
        <EmptyState
          icon={<ServerCrash aria-hidden />}
          title="Can't reach the API"
          body={<CodeText>{error}</CodeText>}
        >
          <Button variant="outline" onClick={() => void poll()}>
            <RotateCw aria-hidden /> Retry
          </Button>
        </EmptyState>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            <Stat
              title="Database"
              value={ready?.db ? "Up" : "Down"}
              hint={
                ready
                  ? Object.entries(ready.extensions)
                      .map(([k, v]) => `${k} ${v ? "✓" : "✗"}`)
                      .join(" · ")
                  : undefined
              }
            />
            <Stat title="Latency p50 / p95" value={`${ms(ops?.p50_latency_ms)} / ${ms(ops?.p95_latency_ms)}`} />
            <Stat
              title="Est. cost"
              value={`$${(ops?.total_cost_usd ?? 0).toFixed(4)}`}
              hint={ops?.avg_cost_usd != null ? `$${ops.avg_cost_usd.toFixed(5)} per call` : undefined}
            />
            <Stat title="Calls (last 200)" value={ops?.count ?? 0} />
            <Stat title="Cache hit rate" value={pct(ops?.cache_hit_rate)} />
            <Stat title="Fallback rate" value={pct(ops?.fallback_rate)} />
            <Stat title="Error rate" value={pct(ops?.error_rate)} />
            <Stat
              title="Calls by kind"
              value={Object.keys(ops?.by_kind ?? {}).length || "—"}
              hint={Object.entries(ops?.by_kind ?? {})
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            />
          </div>
          <DimensionPassRates rates={ops?.runs?.dimension_pass_rates} />
          <AdStudioCard adstudio={ready?.adstudio} />
          <ProvidersCard providers={ready?.providers ?? []} detail={ready?.detail} />
        </>
      )}
    </div>
  );
}
