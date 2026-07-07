/** chatStream — cold-start / resume robustness for the chat turn SSE.
 *
 *  Pins the release-eyeball defect fix: a fresh user's first message spawns a cold-starting worker; the
 *  SSE can close before any token, or drop mid-turn — the turn is NOT lost (durable, resumable output
 *  Stream). These tests fake the SSE/fetch and prove:
 *    1. a slow-starting stream (first token after an early close) shows "starting", resumes, and renders;
 *    2. a mid-stream drop reconnects with Last-Event-ID and continues gaplessly (no duplicate dispatch,
 *       no false "no output" error);
 *    3. a genuine terminal-with-no-output past the hard cap surfaces a failure (fail-loud, not a hang).
 */
import { describe, it, expect, vi } from "vitest";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";

/** Build a Response whose body streams the given SSE text (optionally in several chunks), then closes. */
function sseResponse(chunks: string[], ok = true, status = 200): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) { controller.enqueue(enc.encode(chunks[i++])); }
      else { controller.close(); }
    },
  });
  return { ok, status, body } as unknown as Response;
}

/** A Response whose body streams the given SSE text, then ERRORS the stream (reader.read() rejects) —
 *  models a mid-stream network drop, distinct from a clean close. */
function sseThenError(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else controller.error(new Error("network error"));
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

function ev(o: Record<string, unknown>, id?: string): string {
  return (id ? `id: ${id}\n` : "") + `data: ${JSON.stringify(o)}\n\n`;
}

/** Collect the callback effects into a simple record for assertions. */
function recorder() {
  const state = { text: "", tools: [] as string[], commit: undefined as string | undefined, rejected: false, error: "", modelFailure: 0, starting: 0 };
  const cb: ChatStreamCallbacks = {
    onStarting: () => { state.starting += 1; },
    onDelta: (t) => { state.text += t; },
    onTool: (t) => { state.tools.push(t); },
    onCommit: (sha) => { state.commit = sha; },
    onRejected: () => { state.rejected = true; },
    onModelFailure: () => { state.modelFailure += 1; },
    onError: (m) => { state.error += m; },
  };
  return { state, cb };
}

const noWait = { now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 };

describe("streamChatTurn — cold start & resume", () => {
  it("resumes after an early close (cold start) and renders the delayed reply — no false failure", async () => {
    // Attempt 1: the worker is still cold-starting → the stream closes with NO data (empty body).
    // Attempt 2 (a resume): the first token + completion arrive.
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(sseResponse([]))                                 // early close, no output
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: "hello" }, "5-0"), ev({ type: "turn-complete" }, "6-0")]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.text).toBe("hello");
    expect(result.sawVisibleOutput).toBe(true);
    expect(result.terminal).toBe(true);
    expect(state.starting).toBeGreaterThanOrEqual(1);          // a "starting" affordance was shown
    expect(fetchImpl).toHaveBeenCalledTimes(2);                // one resume happened
  });

  it("sends Last-Event-ID from the last cursor on the resume attempt (gapless reconnect)", async () => {
    // Attempt 1: two deltas arrive (cursors 5-0, 6-0) then the stream DROPS (no terminal event).
    // Attempt 2: the rest streams from the cursor.
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: "par" }, "5-0"), ev({ type: "message-delta", text: "tial" }, "6-0")]))
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: " done" }, "7-0"), ev({ type: "commit", sha: "abc123" }, "8-0"), ev({ type: "turn-complete" }, "9-0")]));
    const { state, cb } = recorder();

    await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.text).toBe("partial done");
    expect(state.commit).toBe("abc123");
    // The resume request carried Last-Event-ID = the last cursor seen on attempt 1 (6-0).
    const secondCallHeaders = (fetchImpl.mock.calls[1][1] as RequestInit).headers as Record<string, string>;
    expect(secondCallHeaders["Last-Event-ID"]).toBe("6-0");
    // The first request carried NO Last-Event-ID (a fresh dispatch, not a resume).
    const firstCallHeaders = (fetchImpl.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(firstCallHeaders["Last-Event-ID"]).toBeUndefined();
  });

  it("surfaces a proxy stream error (terminal) instead of resuming forever", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(sseResponse([ev({ type: "error", message: "agent-api chat returned 502" })]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.error).toContain("502");
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);  // a hard error is terminal — no resume loop
  });

  it("gives up (not an infinite loop) once past the hard cap with no output", async () => {
    // Every attempt closes empty. A fake clock jumps past the hard cap after the first attempt so the
    // loop terminates deterministically; the caller then renders the timed-out message.
    let t = 0;
    const fetchImpl = vi.fn().mockResolvedValue(sseResponse([]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, hardTimeoutMs: 10, reconnectBackoffMs: 0, now: () => (t += 100), sleep: async () => {} },
    );

    expect(result.sawVisibleOutput).toBe(false);
    expect(result.terminal).toBe(false);
    expect(state.error).toBe("");           // the caller (Chat.send) renders the timeout copy, not onError
  });

  it("reconnects when an OPEN stream STALLS (no bytes, no close) — recovers a silently-broken SSE", async () => {
    // Attempt 1: the stream opens but never sends a byte and never closes (half-open / stalled SSE). The
    // old code would block on reader.read() forever; now the idle deadline forces a cursor-resume.
    const stalled = { ok: true, status: 200, body: new ReadableStream<Uint8Array>({ pull() { return new Promise<void>(() => {}); } }) } as unknown as Response;
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(stalled)
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: "recovered" }, "1-0"), ev({ type: "turn-complete" }, "2-0")]));
    const statuses: (string | null)[] = [];
    const { state, cb } = recorder();
    cb.onStatus = (p) => statuses.push(p);

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, idleReconnectMs: 8, heartbeatMs: 3, reconnectBackoffMs: 0, now: () => Date.now() },
    );

    expect(state.text).toBe("recovered");           // attempt 2 delivered after the stall reconnect
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);      // the stall FORCED a reconnect (not a hang)
    expect(statuses).toContain("stalled");           // the pane was told the stream stalled
    expect(statuses[statuses.length - 1]).toBe(null); // cleared at the end
  });

  it("resumes on a MID-STREAM network error instead of surfacing 'network error'", async () => {
    // Attempt 1: a delta arrives (cursor 5-0), then the stream ERRORS (reader.read() rejects). The old
    // code let that throw out → the pane showed "network error" and lost the turn. Now it reconnects.
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(sseThenError([ev({ type: "message-delta", text: "par" }, "5-0")]))
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: "tial done" }, "6-0"), ev({ type: "turn-complete" }, "7-0")]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.text).toBe("partial done");   // resumed and completed — not cut off
    expect(state.error).toBe("");              // the network drop was NOT surfaced as a terminal error
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect((fetchImpl.mock.calls[1][1] as RequestInit).headers as Record<string, string>).toMatchObject({ "Last-Event-ID": "5-0" });
  });

  it("resumes when the POST itself fails (transient), not a terminal error", async () => {
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(sseResponse([ev({ type: "message-delta", text: "ok" }, "1-0"), ev({ type: "turn-complete" }, "2-0")]));
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.text).toBe("ok");
    expect(state.error).toBe("");
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);  // the failed POST was retried, not surfaced
  });

  it("surfaces a 4xx as terminal (a real client error — does not retry forever)", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce({ ok: false, status: 401, body: null } as unknown as Response);
    const { state, cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: new AbortController().signal, ...noWait },
    );

    expect(state.error).toContain("401");
    expect(result.terminal).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);  // 4xx is terminal — no resume loop
  });

  it("stops resuming when the caller aborts", async () => {
    const ctrl = new AbortController();
    const fetchImpl = vi.fn().mockImplementation(async () => { ctrl.abort(); return sseResponse([]); });
    const { cb } = recorder();

    const result = await streamChatTurn(
      { prompt: "hi", session: "s1", active: undefined },
      cb,
      { fetchImpl: fetchImpl as unknown as typeof fetch, signal: ctrl.signal, ...noWait },
    );

    expect(result.aborted).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);  // aborted → the loop exits, no resume
  });
});
