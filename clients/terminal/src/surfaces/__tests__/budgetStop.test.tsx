/** A TURN THAT RAN OUT OF BUDGET SAYS SO, AND OFFERS TO CARRY ON (Vexa-ai/vexa#1622).
 *
 *  Four friction reports were auto-filed from the founder's own chats on 2026-09-06 — three in a row
 *  in one conversation, while he built the OeNB workspace. Each turn spent its 40-call budget and
 *  ENDED, and the chat rendered a finished turn: no line, no affordance, nothing saying the work had
 *  stopped halfway. So he re-typed his instruction into the same wall, three times.
 *
 *  The whole `Chat` is mounted rather than a seam pulled out of it, for the same reason
 *  `actWhileBusy.test.tsx` mounts it: the harness already emitted `turn-truncated`, the stream
 *  already carried `done.reason`, and the bubble already had a place to put it — the defect lived in
 *  the wiring, and only a mounted chat can see wiring.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent } from "@testing-library/react";

/** The stream, faked — the same shape `actWhileBusy.test.tsx` uses, so the two files do not teach
 *  two different fakes for one seam. `vi.hoisted` because `vi.mock`'s factory is hoisted. */
const stream = vi.hoisted(() => ({
  calls: [] as {
    req: { prompt: string; intent?: { kind: string; path?: string; workspace?: string; instruction?: string } };
    opts: { attachFrom?: string };
    cb: Record<string, ((...a: never[]) => void) | undefined>;
    finish: () => void;
  }[],
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown, cb: unknown, opts: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({
        req: req as never,
        opts: (opts ?? {}) as never,
        cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }),
      });
    }),
}));

import { Chat } from "../chat";
import { postIntent } from "../../minutes/extend";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import { ASK_CHAT_EVENT } from "../../canvas/actions";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat() {
  seq += 1;
  render(
    <ServicesProvider container={container()}>
      <Chat params={{ session: `budget-test-${seq}` }} />
    </ServicesProvider>,
  );
}

/** The stop, exactly as the harness sends it: the line, the count, and the act. */
const STOP = {
  steps: 40, budget: 40,
  act: { label: "Continue", instruction: "continue where you stopped" },
};
const LINE = "stopped at the tool-call budget after 40 of 40 steps";

/** Drive one call to its budget stop and let the send finish, as the real stream does. */
async function stopAtBudget(i = 0, partial = "I made a start on the workspace") {
  await act(async () => {
    stream.calls[i].cb.onTruncated?.(LINE as never, partial as never, STOP as never);
    stream.calls[i].finish();
  });
}

beforeEach(() => {
  stream.calls.length = 0;
  try { localStorage.clear(); } catch { /* jsdom always has one */ }
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u.startsWith("/api/chat/submit")) {
      return { ok: true, status: 200, json: async () => ({ ok: true, pending: [], cursor: "9-0" }) };
    }
    if (u.startsWith("/api/chat/pending")) {
      return { ok: true, status: 200, json: async () => ({ pending: [], cursor: "9-0" }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("a turn that hits its tool-call budget", () => {
  it("ends with a visible line naming the budget and the count", async () => {
    mountChat();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "build the OeNB workspace" } }));
    });
    await waitFor(() => expect(stream.calls.length).toBe(1));

    await stopAtBudget();

    // THE LINE — the whole of what three dead turns did not say.
    expect(await screen.findByText(LINE)).not.toBeNull();
    // …and the partial answer is kept beside it, not replaced by it: half an answer is still an
    // answer, it is just not the whole one.
    expect(await screen.findByText(/I made a start on the workspace/)).not.toBeNull();
  });

  it("offers a Continue act that re-submits the SAME TARGET with 'continue where you stopped'", async () => {
    mountChat();
    await act(async () => { postIntent({ kind: "extend", workspace: "oenb-b5e60c", path: "README.md" }); });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await stopAtBudget();

    const button = await screen.findByText("Continue");
    await act(async () => { fireEvent.click(button); });

    // ONE PRESS, and it goes back to the page the stopped act was writing — the target is what
    // makes the worker queue it behind anything still working on that page (Vexa-ai/vexa#1610).
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.intent).toEqual({
      kind: "extend", workspace: "oenb-b5e60c", path: "README.md",
      instruction: "continue where you stopped",
    });
    expect(stream.calls[1].req.prompt).toContain("continue where you stopped");
  });

  it("continues a typed turn as a plain message — the chat is its own target", async () => {
    mountChat();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "write up the meeting" } }));
    });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await stopAtBudget();

    await act(async () => { fireEvent.click(await screen.findByText("Continue")); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.intent).toBeUndefined();
    expect(stream.calls[1].req.prompt).toContain("continue where you stopped");
  });

  it("draws no control when the turn stopped for something there is no continuing", async () => {
    // A context trim, or a deployment one release behind: `done.reason` with no act. The line still
    // says what happened — a button that posted nothing would be worse than the silence it replaces.
    mountChat();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "go" } }));
    });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => {
      stream.calls[0].cb.onTruncated?.("context-trimmed: 3 message(s) dropped" as never, "" as never,
                                       { steps: 4 } as never);
      stream.calls[0].finish();
    });

    expect(await screen.findByText(/context-trimmed/)).not.toBeNull();
    expect(screen.queryByText("Continue")).toBeNull();
  });
});
