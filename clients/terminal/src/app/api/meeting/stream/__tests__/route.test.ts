import { afterEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import { GET } from "../route";

/**
 * Regression test for the half-open SSE proxy bug.
 *
 * The proxy forwards agent-api's live SSE stream to the browser's EventSource.
 * Previously it returned `upstream.body` straight to the Response, so when the
 * upstream stream ended or errored mid-flight, the downstream ReadableStream the
 * browser was reading from never closed/errored. The EventSource stayed half-open
 * for minutes — no `onerror`, no native reconnect.
 *
 * These tests mock `fetch` with an upstream Response whose body is a ReadableStream
 * we control, then drive the real GET handler and assert the *downstream* stream
 * terminates (closes on upstream-done, errors on upstream-error) instead of hanging.
 * No real network is used.
 */

/** Build a minimal NextRequest-shaped object the handler actually touches. */
function makeReq(search = "?meetingId=m1", lastEventId?: string): NextRequest {
  const ctrl = new AbortController();
  return {
    nextUrl: { search, searchParams: new URLSearchParams(search) },
    headers: new Headers(lastEventId ? { "last-event-id": lastEventId } : {}),
    signal: ctrl.signal,
    // expose the controller so a test can simulate the client disconnecting
    _clientAbort: ctrl,
  } as unknown as NextRequest;
}

/** Drain a ReadableStream to completion, collecting decoded text. Resolves on close,
 *  rejects on error. Wrapped in a timeout so a *hang* (the bug) fails loudly. */
async function drain(stream: ReadableStream<Uint8Array>): Promise<{ ok: true; text: string } | { ok: false; err: unknown }> {
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let text = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return { ok: true, text };
      if (value) text += dec.decode(value, { stream: true });
    }
  } catch (err) {
    return { ok: false, err };
  }
}

function withTimeout<T>(p: Promise<T>, ms = 1000): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error("downstream stream hung (half-open)")), ms)),
  ]);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("meeting stream SSE proxy — downstream termination", () => {
  it("closes the downstream stream when the upstream stream ends (reader done)", async () => {
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: {\"type\":\"transcript\"}\n\n"));
        // Upstream ends mid-flight (agent-api closed the connection).
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(upstreamBody, { status: 200 })));

    const res = await GET(makeReq());
    expect(res.body, "proxy should return a streaming body").toBeTruthy();

    const result = await withTimeout(drain(res.body as ReadableStream<Uint8Array>));
    expect(result.ok, "downstream must close cleanly when upstream ends").toBe(true);
    if (result.ok) expect(result.text).toContain("transcript");
  });

  it("errors the downstream stream when the upstream stream errors mid-flight", async () => {
    const boom = new Error("upstream dropped");
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: {\"type\":\"transcript\"}\n\n"));
        // Upstream errors mid-flight (agent-api restart / network drop).
        controller.error(boom);
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(upstreamBody, { status: 200 })));

    const res = await GET(makeReq());
    expect(res.body).toBeTruthy();

    const result = await withTimeout(drain(res.body as ReadableStream<Uint8Array>));
    expect(result.ok, "downstream must error (not hang) when upstream errors").toBe(false);
  });

  it("forwards Last-Event-ID upstream (header AND ?lid= param) for a gapless resume", async () => {
    let upstreamHeaders: Record<string, string> | undefined;
    const mk = () => new ReadableStream<Uint8Array>({ start: (c) => c.close() });
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      upstreamHeaders = init?.headers as Record<string, string>;
      return new Response(mk(), { status: 200 });
    }));
    // via header (browser-native auto-reconnect)
    await GET(makeReq("?meeting_id=m1", "12-0|3-0"));
    expect(upstreamHeaders?.["Last-Event-ID"]).toBe("12-0|3-0");
    // via ?lid= param (the engine's manual forceReconnect path)
    await GET(makeReq("?meeting_id=m1&lid=99-0%7C5-0"));
    expect(upstreamHeaders?.["Last-Event-ID"]).toBe("99-0|5-0");
  });

  it("aborts the upstream fetch when the client disconnects", async () => {
    let upstreamSignal: AbortSignal | undefined;
    // Upstream body that never ends on its own — only an abort can unblock it.
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: {\"type\":\"hello\"}\n\n"));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        upstreamSignal = init?.signal ?? undefined;
        return new Response(upstreamBody, { status: 200 });
      }),
    );

    const req = makeReq();
    const res = await GET(req);
    expect(res.body).toBeTruthy();

    // The handler must have wired its own AbortController into the upstream fetch.
    expect(upstreamSignal, "upstream fetch must receive an abort signal").toBeInstanceOf(AbortSignal);
    expect(upstreamSignal?.aborted).toBe(false);

    // Simulate the browser disconnecting.
    (req as unknown as { _clientAbort: AbortController })._clientAbort.abort();
    expect(upstreamSignal?.aborted, "client disconnect must abort the upstream fetch").toBe(true);
  });

  it("aborts the upstream fetch when the downstream stream is cancelled", async () => {
    let upstreamSignal: AbortSignal | undefined;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: {\"type\":\"hello\"}\n\n"));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        upstreamSignal = init?.signal ?? undefined;
        return new Response(upstreamBody, { status: 200 });
      }),
    );

    const res = await GET(makeReq());
    const body = res.body as ReadableStream<Uint8Array>;
    const reader = body.getReader();
    await reader.read(); // pull the first chunk so the pump is engaged
    await reader.cancel("browser gone");

    expect(upstreamSignal?.aborted, "downstream cancel must abort the upstream fetch").toBe(true);
  });

  it("returns an SSE error frame (does not hang) when the upstream fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("connect ECONNREFUSED");
    }));

    const res = await GET(makeReq());
    const text = await res.text();
    expect(text).toContain("stream-error");
    expect(text).toContain("ECONNREFUSED");
    expect(res.headers.get("Content-Type")).toBe("text/event-stream");
  });
});
