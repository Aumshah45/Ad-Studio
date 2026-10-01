"use client";

import { ArrowLeft, FileText, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { CodeText } from "@/components/code-text";
import { EmptyState } from "@/components/empty-state";
import { Markdown } from "@/components/markdown";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { GoldenSources } from "@/lib/api";
import type { Loaded } from "@/lib/evals";
import { loadSources } from "@/lib/sources";

function ExternalText({ href, children }: { href: string | null | undefined; children: string }) {
  if (!href) return <>{children}</>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="rounded-sm underline underline-offset-2 outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
    >
      {children}
    </a>
  );
}

/** Per-product source, author and licence, then `SOURCES.md` itself (sanitised Markdown). */
export function SourcesTable({ sources, highlight = null }: { sources: GoldenSources; highlight?: string | null }) {
  return (
    <div className="space-y-6">
      {sources.products.length ? (
        <Table aria-label="Golden product photos">
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Author</TableHead>
              <TableHead>Licence</TableHead>
              <TableHead>Share-alike</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sources.products.map((p) => (
              <TableRow
                key={p.id}
                id={`source-${p.id}`}
                data-product={p.id}
                aria-current={highlight === p.id ? "true" : undefined}
                className="scroll-mt-20 aria-[current]:bg-primary/5"
              >
                <TableCell className="align-top">
                  <div className="font-mono text-xs font-medium">{p.id}</div>
                  {p.name ? <div className="text-sm">{p.name}</div> : null}
                  <div className="max-w-56 text-xs whitespace-normal text-muted-foreground">{p.role}</div>
                </TableCell>
                <TableCell className="max-w-72 align-top text-sm whitespace-normal break-words">
                  <ExternalText href={p.source_url}>{p.source_title}</ExternalText>
                  <div className="font-mono text-xs text-muted-foreground">{p.file}</div>
                </TableCell>
                <TableCell className="max-w-48 align-top text-sm whitespace-normal">{p.author}</TableCell>
                <TableCell className="align-top text-sm">
                  <ExternalText href={p.licence_url}>{p.licence}</ExternalText>
                </TableCell>
                <TableCell className="align-top text-sm" data-col="share-alike">
                  {p.share_alike ? (
                    <span className="rounded-md border border-amber-700/25 bg-amber-700/10 px-1.5 py-0.5 text-xs text-amber-800 dark:text-amber-300">
                      Yes
                    </span>
                  ) : (
                    <span className="text-muted-foreground">No</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">The sources file lists no products.</p>
      )}
      <details className="rounded-lg border">
        <summary className="cursor-pointer rounded-lg px-4 py-2 text-sm font-medium outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
          Full <span className="font-mono text-xs">{sources.path}</span>
        </summary>
        <Markdown className="space-y-3 border-t px-4 py-3 text-sm break-words [&_a]:underline [&_code]:font-mono [&_code]:text-xs [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mt-4 [&_h2]:font-semibold [&_table]:block [&_table]:overflow-x-auto [&_table]:text-xs [&_td]:border [&_td]:p-1.5 [&_th]:border [&_th]:p-1.5 [&_th]:text-left">
          {sources.markdown}
        </Markdown>
      </details>
    </div>
  );
}

/** `/batch/sources`: where every golden reference photo comes from. */
export function SourcesView() {
  const [state, setState] = useState<Loaded<GoldenSources> | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const load = useCallback(async () => setState(await loadSources()), []);
  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);
  // `#source-P2` (from an ad's provenance) marks and scrolls to that row once the table exists.
  const loaded = state?.kind === "ok";
  useEffect(() => {
    if (!loaded) return;
    const id = window.location.hash.replace(/^#source-/, "");
    if (!id || id === window.location.hash) return;
    const t = setTimeout(() => {
      setHighlight(id);
      document.getElementById(`source-${id}`)?.scrollIntoView({ block: "center" });
    }, 0);
    return () => clearTimeout(t);
  }, [loaded]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="space-y-1">
        <Link
          href="/batch"
          className="inline-flex items-center gap-1 rounded-sm text-sm text-muted-foreground outline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <ArrowLeft className="size-3.5" aria-hidden /> Batch
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Sources and licences</h1>
        <p className="text-sm text-muted-foreground">
          Where each golden product photo comes from, who made it and under which licence.
        </p>
      </div>
      {state === null ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading sources">
          <Skeleton className="h-10 motion-reduce:animate-none" />
          <Skeleton className="h-48 motion-reduce:animate-none" />
        </div>
      ) : state.kind === "no-report" ? (
        <EmptyState icon={<FileText aria-hidden />} title="No sources file" body={state.message} />
      ) : state.kind === "error" ? (
        <div className="space-y-3">
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t load the sources</AlertTitle>
            <AlertDescription>
              <CodeText>{state.message}</CodeText>
            </AlertDescription>
          </Alert>
          <Button
            variant="outline"
            onClick={() => {
              setState(null);
              void load();
            }}
          >
            <RotateCw aria-hidden /> Retry
          </Button>
        </div>
      ) : (
        <SourcesTable sources={state.data} highlight={highlight} />
      )}
    </div>
  );
}
