/** AN ACT — OR A SENTENCE — SUBMITTED WHILE THE CHAT IS WORKING.
 *
 *  Vexa-ai/vexa#1594, founder walk 2026-09-06: *"extend this page button does not work when chat is
 *  working"*. With a turn in flight, pressing Extend under a page did nothing at all — no bubble, no
 *  row, no error, nothing on the wire. The press reached `postIntent`, the event reached `onAsk`,
 *  `onAsk` called `send`, and `send`'s first line returns on `state.busy`. Three layers each behaved
 *  reasonably and the act evaporated between two of them, under a control that looked exactly as it
 *  does when it works.
 *
 *  Vexa-ai/vexa#1610 kept that rule and moved WHERE the waiting happens. #1594's queue was a list in
 *  ONE TAB: another device never saw the press, a cleared browser never fired it, and nothing
 *  anywhere recorded that it had existed — which is why the founder could not answer *"can i be sure
 *  everything submitted there is actually processed?"*. Now the submission goes to the SERVER at
 *  once, the row that says it is waiting is reconciled against the server's own pending list, and
 *  when the turn in front of it ends the chat ATTACHES to watch it run rather than sending it again.
 *
 *  The whole `Chat` is mounted rather than a seam extracted from it, because every one of the three
 *  layers was individually fine: the defect only ever existed in the wiring between them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";

/** The stream, faked: a turn that does not end until this test says so. `vi.hoisted` because
 *  `vi.mock`'s factory is hoisted above every other statement in the file. */
const stream = vi.hoisted(() => ({
  calls: [] as {
    req: { prompt: string; intent?: { kind: string; path?: string; workspace?: string } };
    opts: { attachFrom?: string };
    cb: Record<string, ((...a: never[]) => void) | undefined>;
    finish: () => void;
  }[],
}));

/** agent-api's INBOX, faked — `/api/chat/submit` appends and `/api/chat/pending` reads back. That
 *  round trip is the whole of what this surface depends on, so it is the whole of what is faked. */
const inbox = vi.hoisted(() => ({
  items: [] as { entry: string; id: string; kind: string; target: string; display: string; at: number }[],
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
  inbox.items.length = 0;
  try { localStorage.clear(); } catch { /* jsdom always has one */ }
  globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
    const u = String(url);
    if (u.startsWith("/api/chat/submit")) {
      const b = JSON.parse(String(init?.body ?? "{}")) as {
        prompt?: string; turn_id?: string; intent?: { kind: string; workspace?: string; path?: string };
      };
      inbox.items.push({
        entry: `${inbox.items.length + 1}-0`, id: b.turn_id ?? "", kind: b.intent?.kind ?? "",
        target: b.intent ? [b.intent.workspace, b.intent.path].filter(Boolean).join("/") : "",
        display: b.prompt ?? "", at: Date.now() / 1000,
      });
      return { ok: true, status: 200, json: async () => ({ ok: true, pending: [...inbox.items], cursor: "9-0" }) };
    }
    if (u.startsWith("/api/chat/pending")) {
      return { ok: true, status: 200, json: async () => ({ pending: [...inbox.items], cursor: "9-0" }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const submits = () =>
  (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
    .filter((c) => String(c[0]).startsWith("/api/chat/submit"));

describe("Extend / Create pressed while a turn is running", () => {
  it("says it is queued AT ONCE, and is on the SERVER at once", async () => {
    mountChat();
    await startATurn();

    await act(async () => { postIntent({ kind: "extend", workspace: "desk", path: "kg/plan.md" }); });

    // 1. THE ROW IS THERE IMMEDIATELY — this is the whole of what the founder was denied on #1594.
    const row = await screen.findByText(/queued behind the current turn/);
    expect(row.textContent).toContain("desk/kg/plan.md");
    // 2. …and it is ON THE SERVER, with its typed intent, before the turn in front of it ends
    //    (Vexa-ai/vexa#1610). Not down this stream — the running turn is not interrupted.
    await waitFor(() => expect(submits()).toHaveLength(1));
    const body = JSON.parse(String((submits()[0][1] as RequestInit).body));
    expect(body.intent).toEqual({ kind: "extend", workspace: "desk", path: "kg/plan.md" });
    expect(body.turn_id).toBeTruthy();
    expect(stream.calls.length).toBe(1);
  });

  it("watches the queued act run instead of sending it a second time", async () => {
    mountChat();
    await startATurn();
    await act(async () => { postIntent({ kind: "create", path: "kg/new.md" }); });
    await screen.findByText(/queued behind the current turn/);
    await waitFor(() => expect(submits()).toHaveLength(1));

    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));

    // THE SECOND CALL IS AN ATTACH: no words, and a cursor to resume from. Sending the prompt again
    // would run the act twice — the worker already holds it.
    expect(stream.calls[1].req.prompt).toBe("");
    expect(stream.calls[1].opts.attachFrom).toBe("42-0");   // the cursor the first stream ended on
    expect(submits()).toHaveLength(1);
  });

  it("hands the queued row over to the job rather than drawing a second one", async () => {
    mountChat();
    await startATurn();
    await act(async () => { postIntent({ kind: "create", path: "kg/new.md" }); });
    await screen.findByText(/queued behind the current turn/);

    await act(async () => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    inbox.items.shift();   // the worker TOOK it — the server stops listing it, then spawns the job
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

  it("QUEUED BEHIND ANOTHER ACT ON THE SAME PAGE is a row, never a refusal", async () => {
    // Vexa-ai/vexa#1610, the founder's own session: he dropped several Extend acts with their own
    // instruction lines onto one page and read *"There is already something running on … — I'll
    // finish that one first"* twice. The second act now waits, carrying the id it will run under.
    mountChat();
    await act(async () => { postIntent({ kind: "extend", path: "kg/plan.md" }); });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => {
      stream.calls[0].cb.onJobQueued?.({
        jobId: "j-88", kind: "extend", target: "kg/plan.md", ahead: 1,
        line: "Extending kg/plan.md — queued behind the one running.",
      } as never);
    });
    const queued = await screen.findByText(/^job · kg\/plan\.md · queued behind the current turn/);
    expect(queued).not.toBeNull();

    // …and when it starts, that same row is the one that starts.
    await act(async () => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-88", kind: "extend", target: "kg/plan.md", line: "on it" } as never);
    });
    const rows = await screen.findAllByText(/^job · kg\/plan\.md/);
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).not.toMatch(/queued/);
  });

  it("an act pressed while the chat is IDLE still goes straight out, with no queue in between", async () => {
    mountChat();
    await act(async () => { postIntent({ kind: "extend", path: "kg/plan.md" }); });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(screen.queryByText(/queued behind the current turn/)).toBeNull();
    expect(submits()).toHaveLength(0);
  });

  it("a plain mid-turn sentence goes to the server too — a dropped sentence is the same defect", async () => {
    mountChat();
    await startATurn();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "and this as well" } }));
    });
    await waitFor(() => expect(submits()).toHaveLength(1));
    expect(JSON.parse(String((submits()[0][1] as RequestInit).body)).prompt).toContain("and this as well");
    expect(stream.calls.length).toBe(1);   // the turn in front of it is untouched
  });
});
