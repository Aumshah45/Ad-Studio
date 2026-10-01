"use client";

import { Pin } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { TextDiff } from "@/components/run/text-diff";
import type { Evidence, Overlay, OverlayTone } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/*
 * Overlay colours: a 2 px ring in the check's state colour with a 1 px white halo inside and out,
 * so it reads on any photo (docs/ux.md accessibility: overlays ≥ 3:1 against the image).
 */
const RING: Record<OverlayTone, string> = {
  pass: "outline-emerald-600",
  fail: "outline-red-600",
  unverified: "outline-zinc-500 outline-dashed",
  info: "outline-sky-600",
};
const CHIP: Record<OverlayTone, string> = {
  pass: "bg-emerald-700 text-white",
  fail: "bg-red-700 text-white",
  unverified: "bg-zinc-600 text-white",
  info: "bg-sky-700 text-white",
};
const TONE_WORD: Record<OverlayTone, string> = {
  pass: "Pass",
  fail: "Fail",
  unverified: "Unsure",
  info: "Info",
};

function pct(v: number): string {
  return `${(v * 100).toFixed(3)}%`;
}

function OverlayBox({ overlay }: { overlay: Overlay }) {
  const { box, tone, label, dashed } = overlay;
  // Chips sit above the box, or inside it when the box touches the top edge.
  const chipInside = box.y < 0.05;
  return (
    <div
      data-overlay={overlay.id}
      data-tone={tone}
      className={cn(
        "absolute rounded-[3px] outline-2 shadow-[0_0_0_3px_rgb(255_255_255/0.85),inset_0_0_0_1px_rgb(255_255_255/0.85)]",
        RING[tone],
        dashed && "outline-dashed",
      )}
      style={{ left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h) }}
    >
      {label ? (
        <span
          className={cn(
            "absolute left-0 max-w-[16ch] truncate rounded-[3px] px-1 font-mono text-[10px] leading-4 font-medium shadow-sm",
            // Reference areas (the text zone) label their bottom-right corner, clear of findings.
            dashed ? "right-0.5 bottom-0.5 left-auto" : chipInside ? "top-0.5 ml-0.5" : "bottom-full mb-1",
            CHIP[tone],
          )}
        >
          {label}
        </span>
      ) : null}
    </div>
  );
}

/** The quoted evidence, TextDiff or colour swatches, laid over the image so nothing shifts. */
function EvidenceCaption({ evidence, atTop, pinned }: { evidence: Evidence; atTop: boolean; pinned: boolean }) {
  const { textDiff, colours, quote, question, references, inherited, wholeImage } = evidence;
  return (
    <div
      data-slot="evidence-caption"
      className={cn(
        "absolute inset-x-1.5 max-h-[55%] overflow-hidden rounded-md border bg-background px-2 py-1.5 text-xs shadow-sm",
        atTop ? "top-1.5" : "bottom-1.5",
      )}
    >
      <p className="flex items-center gap-1.5 font-medium">
        <span className={cn("inline-block size-2 shrink-0 rounded-full", CHIP[evidence.tone])} aria-hidden />
        <span className="min-w-0 truncate">{evidence.title}</span>
        <span className="text-muted-foreground">· {TONE_WORD[evidence.tone]}</span>
        {pinned ? <Pin className="ml-auto size-3 shrink-0 text-muted-foreground" aria-label="Pinned" /> : null}
      </p>
      {textDiff ? <TextDiff {...textDiff} className="mt-1" /> : null}
      {colours ? (
        <div className="mt-1 space-y-0.5 font-mono text-[11px]">
          {(["reference", "ad"] as const).map((k) => (
            <p key={k} className="flex items-center gap-1">
              <span className="w-14 font-sans text-muted-foreground">{k === "ad" ? "In ad" : "Reference"}</span>
              {colours[k].map((c, i) => (
                <span
                  key={i}
                  className="inline-block size-3.5 rounded-sm ring-1 ring-foreground/20"
                  style={{ backgroundColor: c }}
                  title={c}
                />
              ))}
              {k === "ad" && colours.perColour.length ? (
                <span className="ml-1 text-muted-foreground tabular-nums">
                  ΔE {colours.perColour.map((v) => v.toFixed(1)).join(" · ")}
                </span>
              ) : null}
            </p>
          ))}
        </div>
      ) : null}
      {quote ? <p className="mt-0.5 line-clamp-3 break-words text-foreground">&ldquo;{quote}&rdquo;</p> : null}
      {references?.length ? (
        <p className="mt-0.5 line-clamp-2 break-words text-muted-foreground" data-slot="scale-references">
          Compared with: {references.join(", ")}
        </p>
      ) : null}
      {question ? <p className="mt-0.5 line-clamp-2 break-words text-muted-foreground">Asked: {question}</p> : null}
      {wholeImage ? <p className="mt-0.5 text-muted-foreground">Measured on the whole image; no region to show.</p> : null}
      {inherited ? (
        <p className="mt-0.5 text-muted-foreground">Carried over from the source image (only the text was redrawn).</p>
      ) : null}
    </div>
  );
}

/**
 * The signature element (docs/ux.md): the candidate image with the active check's evidence drawn
 * on it (OCR word boxes and the text zone, the product box with its ΔE, the vision read-back
 * boxes) and its TextDiff or quoted judge evidence in a caption. Nothing is drawn while no check
 * is active. The overlay layer is decorative (`aria-hidden`): every overlay has a text
 * equivalent in its check row.
 */
export function EvidenceImage({
  src,
  width,
  height,
  alt,
  evidence,
  pinned = false,
  maxHeight = 280,
  className,
}: {
  src: string;
  width: number;
  height: number;
  alt: string;
  evidence?: Evidence | null;
  pinned?: boolean;
  /** Rendered long edge cap in px (280 in cards, 512 in the approval card). */
  maxHeight?: number;
  className?: string;
}) {
  const ratio = width > 0 && height > 0 ? width / height : 4 / 5;
  // The frame is exactly the image's box, so 0–1 overlay coordinates line up with the pixels.
  const frameWidth = `min(100%, ${Math.round(maxHeight * ratio)}px)`;
  // Put the caption where it hides the least evidence: on top when every finding is low.
  const centers = (evidence?.overlays ?? []).filter((o) => !o.dashed).map((o) => o.box.y + o.box.h / 2);
  const captionAtTop = centers.length > 0 && centers.every((c) => c > 0.45);
  return (
    <figure
      className={cn("relative mx-auto overflow-hidden rounded-md border bg-muted", className)}
      style={{ aspectRatio: `${width} / ${height}`, width: frameWidth }}
      data-slot="evidence-image"
      data-evidence={evidence?.checkId}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- API-served, already ≤ 1024 px */}
      <img
        src={src}
        alt={alt}
        className="absolute inset-0 size-full object-contain motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150"
      />
      {evidence ? (
        <div
          aria-hidden
          data-slot="evidence-overlays"
          className="pointer-events-none absolute inset-0 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150"
        >
          {evidence.overlays.map((o) => (
            <OverlayBox key={o.id} overlay={o} />
          ))}
          <EvidenceCaption evidence={evidence} atTop={captionAtTop} pinned={pinned} />
        </div>
      ) : null}
    </figure>
  );
}

export interface EvidenceFocus {
  /** Hovered, else focused, else pinned. */
  activeId: string | undefined;
  pinnedId: string | undefined;
  hover: (id: string | undefined) => void;
  focus: (id: string | undefined) => void;
  togglePin: (id: string) => void;
  clearPin: () => void;
}

/** Which check's evidence is drawn: hover and keyboard focus show it, click or Enter pins it. */
export function useEvidenceFocus(): EvidenceFocus {
  const [hovered, setHovered] = useState<string>();
  const [focused, setFocused] = useState<string>();
  const [pinned, setPinned] = useState<string>();
  const togglePin = useCallback((id: string) => setPinned((p) => (p === id ? undefined : id)), []);
  const clearPin = useCallback(() => setPinned(undefined), []);
  return useMemo(
    () => ({
      activeId: hovered ?? focused ?? pinned,
      pinnedId: pinned,
      hover: setHovered,
      focus: setFocused,
      togglePin,
      clearPin,
    }),
    [hovered, focused, pinned, togglePin, clearPin],
  );
}
