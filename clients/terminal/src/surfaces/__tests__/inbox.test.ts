/** THE CHAT'S INBOX, CLIENT SIDE (Vexa-ai/vexa#1610).
 *
 *  The founder, dropping acts onto a page while a job ran: *"i drop new tasks to that chat, can i be
 *  sure everything submitted there is actually processed?"* Half the reason the answer was no lived
 *  in this client: a message typed mid-turn was queued in `localStorage` and sent when the turn
 *  ended — a queue with one reader, in one tab. Another device never saw it; a cleared browser never
 *  sent it.
 *
 *  What is pinned here is the shape that replaced it, and it is deliberately the small pure half:
 *  the ROWS come from the server's pending list rather than from anything this client remembers, and
 *  a POST that died in flight is re-sent rather than lost. The wiring — a press mid-turn reaching
 *  `/api/chat/submit`, the chat attaching instead of re-sending — is `__tests__/actWhileBusy.test.tsx`,
 *  where the whole `Chat` is mounted.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  claimInboxRow, fetchPending, flushOutbox, inboxRows, readOutbox, reconcileInbox, submitToInbox,
  type InboxItem,
} from "../inbox";
import { QUEUED_LINE, jobLine, type JobRec } from "../jobs";

const item = (over: Partial<InboxItem> = {}): InboxItem => ({
  entry: "1-0", id: "c-1", kind: "", target: "", display: "and what about pricing?", at: 1, ...over,
});

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as unknown as Response;

beforeEach(() => { localStorage.clear(); });

describe("the rows come from the SERVER's pending list", () => {
  it("draws one row per pending item — an act by its target, a message by the person's words", () => {
    const rows = inboxRows([
      item({ id: "c-1", kind: "extend", target: "desk/kg/plan.md", display: "Extend: kg/plan.md" }),
      item({ entry: "2-0", id: "c-2", display: "and what about pricing?" }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["c-1", "c-2"]);
    expect(rows.every((r) => r.queued && r.inbox)).toBe(true);
    // and they READ as what they are — the person can count what is waiting
    expect(jobLine(rows)).toBe(
      `job · desk/kg/plan.md · ${QUEUED_LINE}   queued · and what about pricing? · ${QUEUED_LINE}`);
  });

  it("names a message row by the person's own words, cut to a label", () => {
    const long = "a".repeat(200);
    expect(inboxRows([item({ display: long })])[0].target).toBe("a".repeat(60) + "…");
  });

  it("REPLACES the server's rows and touches nothing else", () => {
    // a job this client is watching is this client's — it has the events. A queued row is the
    // server's, because the server is the only thing that knows whether a worker has taken it.
    const running: JobRec = { id: "j-1", kind: "extend", target: "desk/a.md", steps: 3, label: "Write" };
    const stale: JobRec = { id: "c-old", kind: "extend", target: "desk/b.md", steps: 0, label: "", queued: true, inbox: true };
    const next = reconcileInbox([running, stale], [item({ id: "c-9", kind: "create", target: "desk/c.md" })]);
    expect(next.map((r) => r.id)).toEqual(["j-1", "c-9"]);
    expect(next[0]).toBe(running);       // untouched, same object
  });

  it("an empty server list clears every queued row and leaves the running ones", () => {
    const running: JobRec = { id: "j-1", kind: "extend", target: "desk/a.md", steps: 1, label: "" };
    const queued: JobRec = { id: "c-1", kind: "extend", target: "desk/b.md", steps: 0, label: "", queued: true, inbox: true };
    expect(reconcileInbox([running, queued], []).map((r) => r.id)).toEqual(["j-1"]);
  });

  it("a job that starts claims the row that was waiting for its target — one act, one row", () => {
    const rows = inboxRows([
      item({ id: "c-1", kind: "extend", target: "desk/a.md" }),
      item({ entry: "2-0", id: "c-2", kind: "extend", target: "desk/a.md" }),
      item({ entry: "3-0", id: "c-3", kind: "extend", target: "desk/b.md" }),
    ]);
    // the FIRST match only: two acts queued on one page are two rows, and the second still waits
    expect(claimInboxRow(rows, "desk/a.md").map((r) => r.id)).toEqual(["c-2", "c-3"]);
    expect(claimInboxRow(rows, "desk/nothing.md")).toBe(rows);
  });
});

describe("a submission is on the server, or still in the outbox", () => {
  it("POSTs the submission and clears the unsent copy on the ack", async () => {
    const fetchImpl = vi.fn(async () => ok({ ok: true, pending: [item()], cursor: "9-0" })) as unknown as typeof fetch;
    const view = await submitToInbox({ id: "c-1", session: "main", prompt: "hello" }, fetchImpl);

    const [url, init] = (fetchImpl as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(url).toBe("/api/chat/submit");
    expect(JSON.parse(String(init.body))).toMatchObject({ prompt: "hello", session: "main", turn_id: "c-1" });
    expect(view.pending).toHaveLength(1);
    expect(readOutbox("main")).toEqual([]);     // acknowledged → nothing left to re-send
  });

  it("KEEPS the unsent copy when the POST never lands — the one gap the server cannot see", async () => {
    const dead = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await expect(submitToInbox({ id: "c-1", session: "main", prompt: "hello" }, dead)).rejects.toThrow();
    expect(readOutbox("main").map((s) => s.id)).toEqual(["c-1"]);
  });

  it("A RELOAD LOSES NOTHING: what the dropped POST left behind is re-sent, in order", async () => {
    const dead = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await expect(submitToInbox({ id: "c-1", session: "main", prompt: "one" }, dead)).rejects.toThrow();
    await expect(submitToInbox({ id: "c-2", session: "main", prompt: "two" }, dead)).rejects.toThrow();

    // …the tab is closed and reopened here — everything below reads only what the browser kept.
    const live = vi.fn(async () => ok({ ok: true, pending: [], cursor: "" })) as unknown as typeof fetch;
    expect(await flushOutbox("main", live)).toEqual(["c-1", "c-2"]);
    const sent = (live as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls
      .map((c) => JSON.parse(String(c[1].body)).prompt);
    expect(sent).toEqual(["one", "two"]);
    expect(readOutbox("main")).toEqual([]);
  });

  it("a flush that fails again leaves the queue alone rather than reordering it", async () => {
    const dead = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await expect(submitToInbox({ id: "c-1", session: "main", prompt: "one" }, dead)).rejects.toThrow();
    expect(await flushOutbox("main", dead)).toEqual([]);
    expect(readOutbox("main").map((s) => s.id)).toEqual(["c-1"]);
  });

  it("a refused submission is not silently acknowledged", async () => {
    const refused = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })) as unknown as typeof fetch;
    await expect(submitToInbox({ id: "c-1", session: "main", prompt: "hello" }, refused)).rejects.toThrow(/503/);
    expect(readOutbox("main").map((s) => s.id)).toEqual(["c-1"]);
  });
});

describe("reading the pending list", () => {
  it("asks for this session and returns what the server holds", async () => {
    const fetchImpl = vi.fn(async () => ok({ pending: [item()], cursor: "7-0" })) as unknown as typeof fetch;
    const view = await fetchPending("meet-41", fetchImpl);
    expect((fetchImpl as unknown as { mock: { calls: [string][] } }).mock.calls[0][0])
      .toBe("/api/chat/pending?session=meet-41");
    expect(view).toEqual({ pending: [item()], cursor: "7-0" });
  });

  it("a chat that cannot read its inbox shows no queued rows, and never an error", async () => {
    const dead = vi.fn(async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    expect(await fetchPending("main", dead)).toEqual({ pending: [], cursor: "" });
    const refused = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })) as unknown as typeof fetch;
    expect(await fetchPending("main", refused)).toEqual({ pending: [], cursor: "" });
  });
});
