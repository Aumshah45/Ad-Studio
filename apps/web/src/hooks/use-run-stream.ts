"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { runsGetRun, type RunDetail } from "@/lib/api";
import { API_URL } from "@/lib/api-config";
import {
  applyRunEvent,
  emptyRunState,
  parseRunEvent,
  runStateFromDetail,
  type RunEvent,
  type RunState,
} from "@/lib/run-events";
import { parseSSE } from "@/lib/sse";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface UseRunStreamOptions {
  /** Injected for tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch;
  baseUrl?: string;
  /** Wait before each reconnect attempt; the stream gives up after the last one. */
  retryDelaysMs?: readonly number[];
  /**
   * Debounce before re-reading `GET /v1/runs/{id}` after an event that changes the stored
   * scorecard or cost (evaluation.done, candidate.created, run.finished). The SSE events carry
   * per-dimension verdicts; the snapshot carries every check row and the ledger cost.
   */
  refreshDelayMs?: number;
}

export interface RunStream {
  state: RunState;
  /** Every RunEvent received, in `seq` order, without duplicates. */
  events: RunEvent[];
  status: StreamStatus;
  error: string | null;
  /** Resume from the last event id after the stream gave up. */
  reconnect: () => void;
}

const DEFAULT_RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000];
const DEFAULT_REFRESH_DELAY_MS = 250;
const REFRESH_ON = new Set<RunEvent["type"]>(["candidate.created", "evaluation.done", "run.finished"]);

interface Store {
  state: RunState;
  events: RunEvent[];
}

type Action =
  | { type: "reset"; runId: string }
  | { type: "snapshot"; detail: RunDetail }
  | { type: "event"; event: RunEvent };

function reducer(store: Store, action: Action): Store {
  switch (action.type) {
    case "reset":
      return { state: emptyRunState(action.runId), events: [] };
    case "snapshot": {
      // Replay the events we already hold on top of the snapshot so a late snapshot never
      // rolls the view back.
      const base = runStateFromDetail(action.detail);
      return { ...store, state: store.events.reduce(applyRunEvent, base) };
    }
    case "event": {
      const last = store.events[store.events.length - 1];
      if (last && action.event.seq <= last.seq) return store;
      return {
        state: applyRunEvent(store.state, action.event),
        events: [...store.events, action.event],
      };
    }
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const id = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        resolve();
      },
      { once: true },
    );
  });
}

/**
 * Follow a run: load the `GET /v1/runs/{id}` snapshot, then tail `GET /v1/runs/{id}/events` (SSE).
 * On a dropped connection it reconnects with `Last-Event-ID` set to the last `seq` it applied,
 * so no event is lost or applied twice. The stream ends after `run.finished`.
 */
export function useRunStream(runId: string, options: UseRunStreamOptions = {}): RunStream {
  const {
    fetchImpl,
    baseUrl = API_URL,
    retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
    refreshDelayMs = DEFAULT_REFRESH_DELAY_MS,
  } = options;
  const [store, dispatch] = useReducer(reducer, runId, (id) => ({
    state: emptyRunState(id),
    events: [],
  }));
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attemptKey, setAttemptKey] = useState(0);
  // Compare delays by value so an inline array doesn't restart the stream on every render.
  const retryKey = retryDelaysMs.join(",");
  const lastSeq = useRef(0);
  const finishedRef = useRef(false);

  useEffect(() => {
    lastSeq.current = 0;
    finishedRef.current = false;
    dispatch({ type: "reset", runId });
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const doFetch = fetchImpl ?? ((input, init) => fetch(input, init));
    const delays = retryKey ? retryKey.split(",").map(Number) : [];

    async function loadSnapshot() {
      try {
        const res = await runsGetRun({
          path: { run_id: runId },
          baseUrl,
          fetch: doFetch,
          signal,
        });
        if (res.data && !signal.aborted) dispatch({ type: "snapshot", detail: res.data });
        return res.response?.status ?? null;
      } catch {
        return null;
      }
    }

    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    function scheduleRefresh() {
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        if (!signal.aborted) void loadSnapshot();
      }, refreshDelayMs);
    }

    async function run() {
      setError(null);
      const snapshotStatus = await loadSnapshot();
      if (signal.aborted) return;
      if (snapshotStatus === 404) {
        setStatus("error");
        setError("This run doesn't exist.");
        return;
      }

      let attempt = 0;
      let finished = finishedRef.current;
      while (!signal.aborted && !finished) {
        setStatus(attempt === 0 && lastSeq.current === 0 ? "connecting" : "reconnecting");
        let received = 0;
        try {
          const headers: Record<string, string> = { Accept: "text/event-stream" };
          if (lastSeq.current > 0) headers["Last-Event-ID"] = String(lastSeq.current);
          const res = await doFetch(`${baseUrl}/v1/runs/${encodeURIComponent(runId)}/events`, {
            headers,
            signal,
            cache: "no-store",
          });
          if (res.status === 404) {
            setStatus("error");
            setError("This run doesn't exist.");
            return;
          }
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
          setStatus("open");
          for await (const msg of parseSSE(res.body)) {
            const event = parseRunEvent(msg.data);
            if (!event || event.seq <= lastSeq.current) continue;
            lastSeq.current = event.seq;
            received += 1;
            dispatch({ type: "event", event });
            if (REFRESH_ON.has(event.type)) scheduleRefresh();
            if (event.type === "run.finished") {
              finished = true;
              finishedRef.current = true;
            }
          }
        } catch {
          if (signal.aborted) return;
        }
        if (finished || signal.aborted) break;
        if (received > 0) attempt = 0;
        if (attempt >= delays.length) {
          setStatus("error");
          setError("Lost connection to the run. It continues on the server.");
          return;
        }
        setStatus("reconnecting");
        await sleep(delays[attempt] ?? 0, signal);
        attempt += 1;
      }
      if (!signal.aborted) setStatus("closed");
    }

    void run();
    return () => {
      controller.abort();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [runId, baseUrl, fetchImpl, retryKey, refreshDelayMs, attemptKey]);

  const reconnect = useCallback(() => setAttemptKey((k) => k + 1), []);

  return { state: store.state, events: store.events, status, error, reconnect };
}
