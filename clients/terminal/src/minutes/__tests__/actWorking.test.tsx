/** THE CONTROL SHOWS ITS OWN STATE WHERE IT WAS PRESSED (Vexa-ai/vexa#1604).
 *
 *  Founder, 2026-09-06, after pressing "Create this page" on an empty page: the control stays
 *  exactly as it was while the job runs in the background; the only sign of life is a row in the
 *  chat — *"this thing should indicate it's actually working"*.
 *
 *  THE PANEL AND THE CHAT ARE MOUNTED TOGETHER, on purpose. Every layer here was individually fine
 *  before: the button posted, the chat sent, the worker reported. What was missing lived in the
 *  wiring between them — the job's events reached the transcript and nothing carried them back to
 *  the thing that was pressed. So these drive real job events through the real chat and read the
 *  real control, rather than calling the store and believing it.
 *
 *  The four states, and the fifth thing that is not a state: working (with the job's step count),
 *  queued, landed (the control is a control again), failed (one line, the act offered again) — and
 *  a second press while working, which does nothing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";

/** The stream, faked: a turn whose callbacks this test drives by hand. `vi.hoisted` because
 *  `vi.mock`'s factory is hoisted above every other statement in the file. */
const stream = vi.hoisted(() => ({
  calls: [] as { cb: Record<string, ((...a: never[]) => void) | undefined>; finish: () => void }[],
}));

vi.mock("../../surfaces/chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/chatStream")>()),
  streamChatTurn: (_req: unknown, cb: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({
        cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false }),
      });
    }),
}));

// The rendered document as one text node — these are about the ACTION, not about how MDX splits a
// paragraph (same reason as `extendPanel.test.tsx`).
vi.mock("../../ui-kit/MdxDoc", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  const { createElement } = await import("react");
  return { ...orig, MdxDoc: (p: { children?: unknown }) => createElement("div", { "data-mdx": "" }, p.children as never) };
});

import { Chat } from "../../surfaces/chat";
import { PagesPanel } from "../PagesPanel";
import { resetActs } from "../../surfaces/actState";
import { clearPending } from "../extend";
import { QUEUED_LINE } from "../../surfaces/jobs";
import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

const PATH = "kg/new.md";
const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

/** A fresh session per test — the chat store is module-level and keyed by (subject, session). */
let seq = 0;
function mount(body: string | null) {
  seq += 1;
  return render(
    <ServicesProvider container={container()}>
      <Chat params={{ session: `act-state-${seq}` }} />
      <PagesPanel pages={[{ path: PATH, label: "New" }]} docPath={PATH} onOpen={() => {}} body={body} />
    </ServicesProvider>,
  );
}

type Which = "create" | "extend";
const control = (w: Which) => document.querySelector(`[data-doc-act="${w}"]`) as HTMLElement;
const state = (w: Which) => control(w)?.getAttribute("data-act-state");
const head = (w: Which) => (control(w)?.querySelector("[data-act-title]") as HTMLElement | null)?.textContent;
const line = (w: Which) => (control(w)?.querySelector("[data-act-line]") as HTMLElement | null)?.textContent;

const flush = async (fn: () => void) => { await act(async () => { fn(); }); };

/** Press it. A press opens the optional one-line field (#1593) and Escape fires the act without a
 *  line — which is the act exactly as it behaved before that field existed. */
async function press(w: Which) {
  await flush(() => { fireEvent.click(control(w)); });
  const field = document.querySelector("[data-act-field]") as HTMLElement | null;
  if (field) await flush(() => { fireEvent.keyDown(field, { key: "Escape" }); });
}

/** agent-api's INBOX, faked (Vexa-ai/vexa#1610): `/api/chat/submit` appends, `/api/chat/pending`
 *  reads back. An act pressed mid-turn goes there instead of into a list in this tab, so a control
 *  that says "queued" is now saying something the SERVER agrees with. */
const inbox: { items: { entry: string; id: string; kind: string; target: string; display: string; at: number }[] } = { items: [] };

beforeEach(() => {
  stream.calls.length = 0;
  inbox.items.length = 0;
  resetActs();
  clearPending();
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

describe("an act control while its job runs", () => {
  it("WORKING: says so from the press, then counts the job's own steps", async () => {
    mount(null);
    await press("create");

    // 1. AT THE PRESS — before the wire, before the job has an id. This is the moment the founder
    //    pressed into and got nothing back.
    expect(state("create")).toBe("working");
    expect(head("create")).toBe("Creating…");

    await waitFor(() => expect(stream.calls.length).toBe(1));
    await flush(() => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-9", kind: "create", target: PATH, line: "on it" } as never);
    });
    expect(head("create")).toBe("Creating…");

    // 2. THE JOB'S OWN STEPS, live, in the same vocabulary the chat's row uses.
    await flush(() => { stream.calls[0].cb.onJobStep?.("j-9" as never, "Write" as never); });
    expect(line("create")).toBe("1 step · Write");
    await flush(() => { stream.calls[0].cb.onJobStep?.("j-9" as never, "Read" as never); });
    expect(line("create")).toBe("2 steps · Read");

    // …and the chat is saying the same thing at the foot of the transcript, about the same page
    expect(screen.getByText(/^job · kg\/new\.md · 2 steps/)).toBeTruthy();
  });

  it("QUEUED: a turn is in front of it, and the control says so in place", async () => {
    mount(null);
    // the chat is working — the state the founder's press fell through on #1594
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "what is on today?" } }));
    });
    await waitFor(() => expect(stream.calls.length).toBe(1));

    await press("create");
    expect(state("create")).toBe("queued");
    expect(line("create")).toBe(QUEUED_LINE);
    // …and it is ON THE SERVER already (Vexa-ai/vexa#1610). It does not go down THIS stream — the
    // turn in front of it is untouched — and it is not held in this tab either: the control says
    // "queued" about something the server is holding.
    await waitFor(() => expect(inbox.items).toHaveLength(1));
    expect(stream.calls.length).toBe(1);

    // the turn ends → the chat ATTACHES to watch the queued act run, and the control learns it is
    // working from the job's own `job-started`, not from a second send.
    await flush(() => { stream.calls[0].finish(); });
    await waitFor(() => expect(stream.calls.length).toBe(2));
    inbox.items.shift();   // the worker took it
    await flush(() => {
      stream.calls[1].cb.onJobStarted?.({ jobId: "j-4", kind: "create", target: PATH, line: "on it" } as never);
    });
    // …and it is working now, not queued: one control, one act, one line running through both
    await waitFor(() => expect(state("create")).toBe("working"));
    expect(head("create")).toBe("Creating…");
  });

  it("LANDED: the record goes and the control is a control again", async () => {
    mount(null);
    await press("create");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await flush(() => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-9", kind: "create", target: PATH, line: "on it" } as never);
    });
    expect(state("create")).toBe("working");

    await flush(() => { stream.calls[0].cb.onJobEnd?.({ jobId: "j-9", ok: true, line: "kg/new.md — written." } as never); });
    await waitFor(() => expect(state("create")).toBeNull());
    expect(head("create")).toBe("Create this page");
    expect(control("create").tagName).toBe("BUTTON");
  });

  it("FAILED: one line of what went wrong, and the act offered again", async () => {
    mount(null);
    await press("create");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await flush(() => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-9", kind: "create", target: PATH, line: "on it" } as never);
    });
    await flush(() => {
      stream.calls[0].cb.onJobEnd?.({ jobId: "j-9", ok: false, line: "Writing kg/new.md failed: the endpoint refused" } as never);
    });

    await waitFor(() => expect(state("create")).toBe("failed"));
    expect(line("create")).toBe("Writing kg/new.md failed: the endpoint refused");
    // OFFERED AGAIN — not a dead control with a red line under it. Pressing it sends the act.
    expect(control("create").tagName).toBe("BUTTON");
    await press("create");
    await waitFor(() => expect(stream.calls.length).toBe(2));
    expect(state("create")).toBe("working");
    expect(line("create")).toBe("");
  });

  it("A SECOND PRESS WHILE WORKING IS INERT — never a second job", async () => {
    mount(null);
    await press("create");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await flush(() => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-9", kind: "create", target: PATH, line: "on it" } as never);
    });

    // the handle is still there — the reader reaches for the same thing — and it does nothing
    await flush(() => { fireEvent.click(control("create")); });
    expect(document.querySelector("[data-act-field]")).toBeNull();
    expect(document.querySelector('[data-doc-act="create-line"]')).toBeNull();
    expect(stream.calls.length).toBe(1);
    expect(state("create")).toBe("working");
  });

  it("EXTEND under a page that exists wears the same four states, in its own words", async () => {
    mount("# New\n\nA page that exists.\n");
    await press("extend");
    expect(state("extend")).toBe("working");
    expect(head("extend")).toBe("Extending…");

    await waitFor(() => expect(stream.calls.length).toBe(1));
    await flush(() => {
      stream.calls[0].cb.onJobStarted?.({ jobId: "j-1", kind: "extend", target: PATH, line: "on it" } as never);
    });
    await flush(() => { stream.calls[0].cb.onJobStep?.("j-1" as never, "Grep" as never); });
    expect(line("extend")).toBe("1 step · Grep");
    await flush(() => { stream.calls[0].cb.onJobEnd?.({ jobId: "j-1", ok: true, line: "extended." } as never); });
    await waitFor(() => expect(head("extend")).toBe("Extend this page"));
  });

  it("a turn that ends with no job at all does not leave the control spinning", async () => {
    // A refusal, an error, or a deployment whose worker still runs the act inline: nothing is
    // running, so nothing may say it is.
    mount(null);
    await press("create");
    await waitFor(() => expect(stream.calls.length).toBe(1));
    expect(state("create")).toBe("working");
    await flush(() => { stream.calls[0].finish(); });
    await waitFor(() => expect(state("create")).toBeNull());
  });
});
