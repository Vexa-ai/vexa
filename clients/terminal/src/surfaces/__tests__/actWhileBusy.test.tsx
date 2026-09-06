/** AN ACT PRESSED WHILE THE CHAT IS WORKING (Vexa-ai/vexa#1594).
 *
 *  Founder walk 2026-09-06: *"extend this page button does not work when chat is working"*. With a
 *  turn in flight, pressing Extend under a page did nothing at all — no bubble, no row, no error,
 *  nothing on the wire. The press reached `postIntent`, the event reached `onAsk`, `onAsk` called
 *  `send`, and `send`'s first line returns on `state.busy`. Three layers each behaved reasonably and
 *  the act evaporated between two of them, under a control that looked exactly as it does when it
 *  works.
 *
 *  These pin the rule that replaced it: the act fires when it is pressed, and when the turn in front
 *  of it has to finish first, the panel SAYS SO — a job row at the foot of the transcript, at once,
 *  reading "queued behind the current turn" — and then the act runs. Never a silent drop, never a
 *  disabled control.
 *
 *  The whole `Chat` is mounted rather than a seam extracted from it, because every one of the three
 *  layers was individually fine: the defect only exists in the wiring between them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";

/** The stream, faked: a turn that does not end until this test says so. `vi.hoisted` because
 *  `vi.mock`'s factory is hoisted above every other statement in the file. */
const stream = vi.hoisted(() => ({
  calls: [] as {
    req: { prompt: string; intent?: { kind: string; path?: string; workspace?: string } };
    cb: Record<string, ((...a: never[]) => void) | undefined>;
    finish: () => void;
  }[],
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (req: unknown, cb: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({
        req: req as never,
        cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false }),
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

/** A fresh session per test — the chat store is module-level and keyed by (subject, session), so a
 *  shared key would carry one test's turns into the next. */
let seq = 0;
function mountChat() {
  seq += 1;
  const session = `busy-test-${seq}`;
  render(
    <ServicesProvider container={container()}>
      <Chat params={{ session }} />
    </ServicesProvider>,
  );
  return session;
}

/** Start an ordinary chat turn — the "chat is working" half of the founder's sentence. */
async function startATurn() {
  await act(async () => {
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "what is on today?" } }));
  });
  await waitFor(() => expect(stream.calls.length).toBe(1));
}

beforeEach(() => {
  stream.calls.length = 0;
  globalThis.fetch = vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }),
  }) as unknown as Response) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Extend / Create pressed while a turn is running", () => {
  it("says it is queued AT ONCE, and then actually sends the act", async () => {
    mountChat();
    await startATurn();

    await act(async () => { postIntent({ kind: "extend", workspace: "desk", path: "kg/plan.md" }); });

    // 1. THE ROW IS THERE IMMEDIATELY — this is the whole of what the founder was denied.
    const row = await screen.findByText(/queued behind the current turn/);
    expect(row.textContent).toContain("desk/kg/plan.md");
    // 2. …and nothing has been sent yet: the turn in front of it is still running.
    expect(stream.calls.length).toBe(1);

    // 3. the turn ends → the act fires as its own turn, carrying its TYPED intent (not a paraphrase)
    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.intent).toEqual({ kind: "extend", workspace: "desk", path: "kg/plan.md" });
    // …and the row STAYS while that send is in flight. It is waiting for its job id, not for its
    // turn: the next test is the handover.
    expect(screen.queryByText(/queued behind the current turn/)).not.toBeNull();
  });

  it("hands the queued row over to the job rather than drawing a second one", async () => {
    mountChat();
    await startATurn();
    await act(async () => { postIntent({ kind: "create", path: "kg/new.md" }); });
    await screen.findByText(/queued behind the current turn/);

    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    await act(async () => {
      stream.calls[1].cb.onJobStarted?.({
        jobId: "j-77", kind: "create", target: "kg/new.md",
        line: "Writing kg/new.md — I'll say when it's there.",
      } as never);
    });

    const rows = await screen.findAllByText(/^job · kg\/new\.md/);
    expect(rows).toHaveLength(1);                       // ONE act, ONE line
    expect(rows[0].textContent).not.toMatch(/queued/);  // and it is running now, not queued
  });

  it("does not leave a row spinning when the turn ends without a job", async () => {
    // A refusal, an error, or a deployment whose worker still runs the act inline: the act is over
    // and nothing ever claimed the row. A row that outlives its act is the same lie as a spinner
    // that outlives its turn.
    mountChat();
    await startATurn();
    await act(async () => { postIntent({ kind: "extend", path: "kg/plan.md" }); });
    await screen.findByText(/queued behind the current turn/);

    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    await act(async () => { stream.calls[1].finish(); });
    await waitFor(() => expect(screen.queryByText(/queued behind the current turn/)).toBeNull());
  });

  it("an act pressed while the chat is IDLE still goes straight out, with no queue in between", async () => {
    mountChat();
    await act(async () => { postIntent({ kind: "extend", path: "kg/plan.md" }); });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(screen.queryByText(/queued behind the current turn/)).toBeNull();
  });

  it("queues a plain mid-turn ask too — a dropped sentence is the same defect without a row", async () => {
    mountChat();
    await startATurn();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "and this as well" } }));
    });
    expect(stream.calls.length).toBe(1);
    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(stream.calls[1].req.prompt).toContain("and this as well");
  });
});
