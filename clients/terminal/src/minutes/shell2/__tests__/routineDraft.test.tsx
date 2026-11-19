/** A DRAFT ROUTINE IS ONE PRESS FROM WHERE ITS PERSON IS (live app.dev 2026-10-18).
 *
 *  The founder's agent filed "narrative-field-pulse" (daily 07:00 UTC, in his chat) as a draft and
 *  told him it waited "on your Routines board". shell2 has no such board; the draft showed nowhere:
 *  no routine mark on the chat (that comes with a run), so the Routines filter skipped it, and the
 *  strip sat behind "…" → Activity. Now:
 *  - the Routines filter lists it under Drafts — Draft tag, schedule, home chat, an on/off switch;
 *  - its chat carries "Draft routine · <name> · <when> · Turn on" at the top;
 *  - the switch and the button are the person's own press (`PATCH …/enabled` from the session —
 *    agent-api refuses an agent turn and an API key, `core/agent/tests/test_s1e_security_floor.py`);
 *  - once on, its chat is a routine's: the icon and the Routines filter. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Rail } from "../../Rail";
import { applyFolders, type RailFolders } from "../../railFolders";
import { withRoutineState } from "../../chats";
import { setFeatureOverride } from "../../../platform/features";
import { resetCentre } from "../centre";
import { ChatRoutineDrafts, ROUTINES_CHANGED_EVENT, routinesOfChat } from "../../RoutineBar";
import { draftLine, draftRoutines, draftWhen, draftsOfChat, isDraft, routineLabel, type RoutineCard } from "../../routineDraft";
vi.mock("../../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

const DRAFT: RoutineCard = { name: "narrative-field-pulse", status: "draft", draft: true, enabled: false,
  cron: "0 7 * * *", trigger: { kind: "schedule", cron: "0 7 * * *" }, thread: "pchat-mve1pu6y",
  flow: { name: "narrative-field-pulse", scope: "user:2", version: 1 } };
const ACTIVE: RoutineCard = { ...DRAFT, status: "active", draft: false, enabled: true };

const F: RailFolders = { served: true, order: {}, filings: {},
  folders: [{ id: "pinned", name: "Pinned", builtin: true, collapsed: false }, { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
    { id: "routines", name: "Routines", builtin: true, collapsed: false }, { id: "hidden", name: "Hidden", builtin: true, collapsed: true }] };
const row = (key: string, extra: Record<string, unknown> = {}) => ({ key, chatId: key.slice(2), label: `Row ${key}`, whenLabel: "1:00 PM", when: 1, ...extra }) as never;
const chatRows = (cards: RoutineCard[]) => applyFolders(withRoutineState(
  [row("c:pchat-mve1pu6y", { label: "Daily narrative field research routine" }), row("c:other")], cards), F);

afterEach(() => { cleanup(); resetCentre(); setFeatureOverride("all"); vi.unstubAllGlobals(); try { localStorage.removeItem("vexa.rail.filter"); } catch { /* */ } });

describe("a draft routine's words", () => {
  it("reads as the founder would: name, schedule on his clock", () => {
    expect(routineLabel("narrative-field-pulse")).toBe("Narrative field pulse");
    expect(draftWhen(DRAFT)).toMatch(/^daily \d{2}:\d{2}$/);
    expect(draftLine(DRAFT)).toMatch(/^Draft routine · Narrative field pulse · daily \d{2}:\d{2}$/);
  });
  it("a draft is not a paused routine, and is not one of its chat's routine strips", () => {
    expect(isDraft(DRAFT)).toBe(true);
    expect(isDraft({ status: "active", draft: false })).toBe(false);
    expect(isDraft({ status: "retired" })).toBe(false);
    expect(routinesOfChat([DRAFT], "pchat-mve1pu6y")).toEqual([]);
    expect(routinesOfChat([ACTIVE], "pchat-mve1pu6y")).toEqual(["narrative-field-pulse"]);
    expect(draftsOfChat([DRAFT], "pchat-mve1pu6y").map((c) => c.name)).toEqual(["narrative-field-pulse"]);
    expect(draftsOfChat([DRAFT], "other")).toEqual([]);
    expect(draftRoutines([DRAFT, ACTIVE])).toHaveLength(1);
  });
});

describe("its home chat: the Turn on line", () => {
  it("shows the draft line with Turn on in its own chat only", () => {
    render(<ChatRoutineDrafts session="pchat-mve1pu6y" cards={[DRAFT]} />);
    expect(screen.getByRole("region", { name: "Draft routine" }).textContent).toMatch(/Draft routine · Narrative field pulse · daily \d{2}:\d{2}/);
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
    cleanup();
    render(<ChatRoutineDrafts session="other" cards={[DRAFT]} />);
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
    cleanup();
    render(<ChatRoutineDrafts session="pchat-mve1pu6y" cards={[ACTIVE]} />);
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
  });
  it("Turn on is the person's press: PATCH …/enabled from the session, and the routines re-read", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => { calls.push({ url, init }); return new Response("{}", { status: 200 }); }));
    const changed = vi.fn();
    window.addEventListener(ROUTINES_CHANGED_EVENT, changed);
    render(<ChatRoutineDrafts session="pchat-mve1pu6y" cards={[DRAFT]} />);
    fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    window.removeEventListener(ROUTINES_CHANGED_EVENT, changed);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/routines/narrative-field-pulse/enabled");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ enabled: true });
  });
  it("a refused press says so and leaves the draft a draft", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { reason: "person_approval_required" } }), { status: 403 })));
    render(<ChatRoutineDrafts session="pchat-mve1pu6y" cards={[DRAFT]} />);
    fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/did not turn on/);
    expect(screen.getByRole("button", { name: "Turn on" })).toBeTruthy();
  });
});

describe("the sidebar's Routines filter (shell2)", () => {
  const rail = (cards: RoutineCard[], onTurnOn = vi.fn(async () => true), onOpen = vi.fn()) => render(
    <Rail rows={chatRows(cards)} folders={F} selKey="" onSelect={() => {}} onNewChat={() => {}} onDeleteChat={() => {}}
      onHome={() => {}} onMoveRows={() => {}}
      routineDrafts={draftRoutines(cards).map((c) => ({ name: c.name, label: routineLabel(c.name), when: draftWhen(c), thread: c.thread }))}
      onTurnOnRoutine={onTurnOn} onOpenThread={onOpen} />);

  it("lists the draft, marked Draft, with its schedule, its home chat and a switch", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    const onTurnOn = vi.fn(async () => true);
    const onOpen = vi.fn();
    rail([DRAFT], onTurnOn, onOpen);
    expect(screen.getByRole("tab", { name: /Routines/ }).textContent).toBe("Routines1");
    fireEvent.click(screen.getByRole("tab", { name: /Routines/ }));
    const item = document.querySelector('[data-routine-draft-row="narrative-field-pulse"]') as HTMLElement;
    expect(item).toBeTruthy();
    expect(item.textContent).toContain("Narrative field pulse");
    expect(item.textContent).toContain("Draft");
    expect(item.querySelector("[data-routine-draft-meta]")!.textContent).toMatch(/^daily \d{2}:\d{2} · in Daily narrative field research routine$/);
    // a draft's chat is not yet a routine's chat
    expect(document.querySelector('[data-row-key="c:pchat-mve1pu6y"]')).toBeNull();
    const sw = screen.getByRole("switch", { name: "Turn on Narrative field pulse" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(sw);
    expect(onTurnOn).toHaveBeenCalledWith("narrative-field-pulse");
    fireEvent.click(item.querySelector(".vx-rail-draft-open")!);
    expect(onOpen).toHaveBeenCalledWith("pchat-mve1pu6y");
  });

  it("once on, its chat shows as a routine: the icon and the Routines filter", () => {
    setFeatureOverride({ home2: true, rooms: true, shell2: true });
    rail([ACTIVE]);
    fireEvent.click(screen.getByRole("tab", { name: /Routines/ }));
    expect(document.querySelector('[data-routine-draft-row]')).toBeNull();
    const r = document.querySelector('[data-row-key="c:pchat-mve1pu6y"]') as HTMLElement;
    expect(r).toBeTruthy();
    expect(r.getAttribute("data-row-routine")).toBe("narrative-field-pulse");
    expect(r.querySelector('[data-row-icon="routine"]')).toBeTruthy();
  });
});
