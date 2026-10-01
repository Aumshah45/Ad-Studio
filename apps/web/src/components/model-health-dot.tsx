"use client";

import { useEffect, useState } from "react";

import { healthReady } from "@/lib/api";
import { cn } from "@/lib/utils";

type Health = "ok" | "degraded" | "unavailable" | "unknown";

const COLORS: Record<Health, string> = {
  ok: "bg-emerald-500",
  degraded: "bg-amber-500",
  unavailable: "bg-red-500",
  unknown: "bg-muted-foreground/40",
};

/** Small dot showing API readiness; polls /ready every 30 s. */
export function ModelHealthDot() {
  const [health, setHealth] = useState<Health>("unknown");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const { data, response } = await healthReady();
        if (!alive) return;
        setHealth(data?.status ?? (response?.status === 503 ? "unavailable" : "unknown"));
      } catch {
        if (alive) setHealth("unavailable");
      }
    };
    void poll();
    const id = setInterval(poll, 30_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
      <span className={cn("size-2 rounded-full", COLORS[health])} aria-hidden />
      API {health}
    </span>
  );
}
