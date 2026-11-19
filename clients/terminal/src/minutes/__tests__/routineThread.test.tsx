/** ADR-0046 decision 12 — a routine's agent is a long-lived chat in the person's list.
 *
 *  The thread arrives from the server's session index marked with its routine; the rail shows it
 *  (by default, untouched or not) with a `routine` tag; deleting it asks whether to retire the
 *  routine too; and the thread carries the routine's pause / resume / retire controls. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { chatsFromSessions, mergeChats, railRows, visibleRows } from "../chats";
import { Rail } from "../Rail";
import { RoutineBar } from "../RoutineBar";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const THREAD = {
  session: "flow-0123456789abcdef", title: "brief", label: "brief", created: 1, last_active: 2,
  touched: false, routine: { flow: "brief", scope: "user:7", version: 2 },
};

describe("a routine's thread on the rail", () => {
  it("is read off the session index and shown by default", () => {
    const chats = chatsFromSessions([THREAD], 10);
    expect(chats[0].routine).toEqual({ flow: "brief", scope: "user:7", version: 2 });
    const rows = railRows(chats, [], 10);
    expect(rows[0].routine?.flow).toBe("brief");
    expect(visibleRows(rows, false)).toHaveLength(1);
  });

  it("keeps the server's marker through a merge with the stored list", () => {
    const local = chatsFromSessions([{ ...THREAD, routine: null }], 10);
    const merged = mergeChats(local, chatsFromSessions([THREAD], 10));
    expect(merged[0].routine?.version).toBe(2);
  });

  it("drops a half marker", () => {
    expect(chatsFromSessions([{ ...THREAD, routine: { flow: "brief" } }], 10)[0].routine)
      .toBeUndefined();
  });
});

describe("deleting a routine's thread asks", () => {
  const rows = railRows(chatsFromSessions([THREAD], 10), [], 10);
  const rail = (onDelete: (id: string, o?: { retireRoutine?: boolean }) => void) => render(
    <Rail rows={rows} hidden={0} all={false} onAll={() => {}} selKey={null} onSelect={() => {}}
      onNewChat={() => {}} onDeleteChat={onDelete} />);

  it("marks the row", () => {
    const { container } = rail(() => {});
    expect(container.querySelector('[data-row-routine="brief"]')).not.toBeNull();
  });

  // The row's × → ✓ became the row's menu → "Delete…" → a dialog (design guidelines §4.11 and
  // Vexa-ai/vexa#1829). For a routine's thread that dialog has no plain "Delete chat": the only ways
  // to delete are the two answers.
  const openDelete = () => {
    fireEvent.click(screen.getByRole("button", { name: "Actions for brief" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete/ }));
  };

  it("never deletes on the confirm click alone, and passes the answer", () => {
    const onDelete = vi.fn();
    const { container } = rail(onDelete);
    openDelete();
    expect(screen.queryByRole("button", { name: "Delete chat" })).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(container.ownerDocument.querySelector("[data-routine-retire]") as HTMLElement);
    expect(onDelete).toHaveBeenCalledWith(THREAD.session, { retireRoutine: true });
  });

  it("can keep the routine running", () => {
    const onDelete = vi.fn();
    rail(onDelete);
    openDelete();
    fireEvent.click(document.querySelector("[data-routine-keep]") as HTMLElement);
    expect(onDelete).toHaveBeenCalledWith(THREAD.session, { retireRoutine: false });
  });
});

describe("the thread's routine controls", () => {
  it("pause, resume and retire go through the routine routes for this flow", async () => {
    const calls: [string, string][] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push([String(init?.method), url]);
      return { ok: true } as Response;
    }));
    const { container } = render(<RoutineBar routine={{ flow: "brief", scope: "user:7", version: 2 }} />);
    fireEvent.click(container.querySelector("[data-routine-expand]") as HTMLElement);  // one line until expanded
    fireEvent.click(screen.getByText("Pause"));
    await waitFor(() => screen.getByText("Resume"));
    fireEvent.click(screen.getByText("Resume"));
    await waitFor(() => screen.getByText("Pause"));
    fireEvent.click(screen.getByText("Retire"));
    fireEvent.click(screen.getByText("Yes, retire"));     // reversible, so a confirmation — not a colour
    await waitFor(() => expect(screen.queryByText("Retire")).toBeNull());
    expect(calls).toEqual([
      ["undefined", "/api/routines"],          // the strip reads why the routine last did not run
      ["PATCH", "/api/routines/brief/enabled"],
      ["PATCH", "/api/routines/brief/enabled"],
      ["DELETE", "/api/routines/flow%3Abrief"],
    ]);
  });
});

describe("a turn another agent sent", () => {
  it("is drawn under who sent it, never as the person's bubble, and links the sending thread", async () => {
    const { AgentMessageBubble } = await import("../../surfaces/chat");
    const seen: string[] = [];
    const on = (e: Event) => seen.push(String((e as CustomEvent).detail?.chat));
    window.addEventListener("vexa:open-chat", on);
    const { container } = render(
      <AgentMessageBubble from={{ uid: "7", thread: "pchat-a", label: "routine brief" }} text="A asks B" />);
    expect(container.querySelector('[data-agent-message="pchat-a"]')).not.toBeNull();
    expect(screen.getByText(/From routine brief/)).toBeTruthy();
    fireEvent.click(container.querySelector('[data-open-thread="pchat-a"]') as HTMLElement);
    expect(seen).toEqual(["pchat-a"]);
    window.removeEventListener("vexa:open-chat", on);
  // the dynamic import of surfaces/chat (the whole chat surface) is most of this test's time; under
  // a loaded full run it alone passes the 5 s default, so this one test is allowed 30 s
  }, 30_000);
});
