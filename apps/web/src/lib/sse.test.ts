import { describe, expect, it } from "vitest";

import { parseSSE, streamEvents } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const x of gen) out.push(x);
  return out;
}

describe("parseSSE", () => {
  it("handles events split across chunk boundaries", async () => {
    const msgs = await collect(
      parseSSE(streamOf(["event: tok", "en\ndata: {\"a\"", ":1}\n", "\nevent: done\ndata: {}\n\n"])),
    );
    expect(msgs).toEqual([
      { event: "token", data: '{"a":1}' },
      { event: "done", data: "{}" },
    ]);
  });

  it("joins multi-line data and skips comments", async () => {
    const msgs = await collect(
      parseSSE(streamOf([": keep-alive\n\n", "data: line1\r\ndata: line2\r\n\r\n"])),
    );
    expect(msgs).toEqual([{ event: "message", data: "line1\nline2" }]);
  });

  it("types events from a Response", async () => {
    const body = 'event: status\ndata: {"type":"status","message":"hi"}\n\n';
    const events = await collect(streamEvents(new Response(streamOf([body]))));
    expect(events[0]).toEqual({ type: "status", message: "hi" });
  });
});
