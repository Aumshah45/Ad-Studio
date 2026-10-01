import type { MetaStreamEventsResponse } from "@/lib/api";

/** One event of the API's `StreamEvent` union (see GET /v1/meta/stream-events). */
export type StreamEvent = MetaStreamEventsResponse[number];

export interface SSEMessage {
  event: string;
  data: string;
  /** The frame's `id:` line, when the server sent one (used as Last-Event-ID on reconnect). */
  id?: string;
}

/**
 * Parse a text/event-stream body. Handles events split across chunks, multi-line `data:`,
 * comment lines (`: keep-alive`) and CRLF line endings.
 */
export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<SSEMessage> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";
  let data: string[] = [];
  let id: string | undefined;

  const flush = (): SSEMessage | null => {
    if (data.length === 0) {
      event = "message";
      id = undefined;
      return null;
    }
    const msg: SSEMessage = { event, data: data.join("\n") };
    if (id !== undefined) msg.id = id;
    event = "message";
    data = [];
    id = undefined;
    return msg;
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.search(/\r?\n/)) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(buffer[newline] === "\r" ? newline + 2 : newline + 1);
        if (line === "") {
          const msg = flush();
          if (msg) yield msg;
        } else if (line.startsWith(":")) {
          continue;
        } else {
          const colon = line.indexOf(":");
          const field = colon === -1 ? line : line.slice(0, colon);
          let val = colon === -1 ? "" : line.slice(colon + 1);
          if (val.startsWith(" ")) val = val.slice(1);
          if (field === "event") event = val;
          else if (field === "data") data.push(val);
          else if (field === "id") id = val;
        }
      }
      if (done) break;
    }
    const last = flush();
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}

/** Typed stream events from a fetch Response. */
export async function* streamEvents(res: Response): AsyncGenerator<StreamEvent> {
  if (!res.body) return;
  for await (const msg of parseSSE(res.body)) {
    try {
      yield JSON.parse(msg.data) as StreamEvent;
    } catch {
      yield { type: "error", code: "bad-event", message: "Malformed event from server" };
    }
  }
}
