/** A turn that compacted older history says so QUIETLY (founder 2026-10-10).
 *
 *  On app.dev a chat's answer ended with "context-trimmed: 85 message(s) dropped to stay inside the
 *  turn's 24000-token budget", rendered as the turn's stop line under the reply. The harness now
 *  carries a count (`done.compacted`) and no `reason`; the client shows a muted note in the
 *  activity line, never in the reply and never as a stop line. */
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup, waitFor } from "@testing-library/react";
import { Conversation, type Turn } from "../../workbench/agent-window";
import { streamChatTurn, type ChatStreamCallbacks } from "../chatStream";

afterEach(() => cleanup());

function sse(events: object[]): Response {
  const enc = new TextEncoder();
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  let sent = false;
  const body = new ReadableStream<Uint8Array>({
    pull(c) { if (!sent) { sent = true; c.enqueue(enc.encode(text)); } else c.close(); },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

describe("a compacted turn", () => {
  it("reaches the client as a count, not as a stop line or a model failure", async () => {
    const seen = { compacted: [] as number[], truncated: [] as string[], failures: 0 };
    const cb: ChatStreamCallbacks = {
      onStarting: () => {}, onDelta: () => {}, onTool: () => {}, onCommit: () => {}, onRejected: () => {},
      onModelFailure: () => { seen.failures += 1; }, onError: () => {},
      onTruncated: (reason) => { seen.truncated.push(reason); },
      onCompacted: (n) => { seen.compacted.push(n); },
    };
    await streamChatTurn({ prompt: "hi", session: "s", active: undefined }, cb,
      { fetchImpl: (async () => sse([{ type: "done", ok: true, reply: "the answer", sessionId: "s", steps: 3, compacted: 85 },
                                      { type: "turn-complete" }])) as unknown as typeof fetch,
        signal: new AbortController().signal, now: () => 0, sleep: async () => {}, reconnectBackoffMs: 0 });
    expect(seen.compacted).toEqual([85]);
    expect(seen.truncated).toEqual([]);
    expect(seen.failures).toBe(0);
  });

  it("renders a muted note in the activity line and leaves the reply alone", async () => {
    const turns: Turn[] = [{ id: "a", role: "agent", text: "the answer", ops: [{ label: "Read notes.md" } as never],
                             steps: 3, compacted: 85 }];
    const { container } = render(<Conversation turns={turns} busy={false} />);
    const note = container.querySelector("[data-compacted]");
    expect(note?.textContent).toContain("older context compacted");
    expect(container.textContent).not.toMatch(/context-trimmed|dropped to stay inside/);
    await waitFor(() => expect(container.textContent).toContain("the answer"));
  });
});
