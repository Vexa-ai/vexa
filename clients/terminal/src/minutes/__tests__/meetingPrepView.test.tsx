/** PREP IN THE MEETING PANEL (`meeting_prep`; validation log O2, founder-felt): driven through the
 *  section, with every owner faked at its own seam — the meetings store (meeting-api's rows), the people
 *  the viewer knows, the card Inbox, the meeting page. Claims:
 *    · the section shows the goal, the people with their sources (names, never handles), the open
 *      cards from the previous occurrence, the agenda, the material, and three ticks;
 *    · "only you" only when the page is the desk copy; a shared page says the goal is shared;
 *    · Set goal writes `goals:` into the page the view read, where it read it — nothing else changes;
 *    · a meeting with no page gets its page minted first (the existing mint), then the goal written;
 *    · the panel shows Prep only with the flag on and only for a planned meeting. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent } from "@testing-library/react";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const s = vi.hoisted(() => ({
  rows: [] as unknown[],
  people: [] as unknown[],
  cards: [] as unknown[],
  writes: [] as { path: string; content: string; slug?: string }[],
  mints: [] as string[],
  file: "" as string,
}));
vi.mock("../../surfaces/liveMeetings", async (orig) => ({ ...(await orig<typeof import("../../surfaces/liveMeetings")>()), useLiveMeetings: () => s.rows }));
vi.mock("../shell2/PeoplePage", async (orig) => ({ ...(await orig<typeof import("../shell2/PeoplePage")>()), usePeople: () => ({ phase: "ready", people: s.people }) }));
vi.mock("../cards", async (orig) => ({ ...(await orig<typeof import("../cards")>()), useCardInbox: () => ({ phase: "ready", items: s.cards, counts: { waiting_on_me: 0, due_soon: 0, i_wait_on: 0 } }) }));
vi.mock("../cards/api", async (orig) => ({ ...(await orig<typeof import("../cards/api")>()), openCardSheet: vi.fn() }));
vi.mock("../../ui-kit/RefChip", async (orig) => ({ ...(await orig<typeof import("../../ui-kit/RefChip")>()), RefChip: ({ text }: { text: string }) => <span data-ref={text}>{text.replace(/^.*\|/, "").replace(/\]\]$/, "")}</span> }));
vi.mock("../shell2/me", async (orig) => ({ ...(await orig<typeof import("../shell2/me")>()), meNow: () => ({ name: "Me", email: "me@v.example", subject: "2" }) }));
vi.mock("../meetingNote", async (orig) => ({ ...(await orig<typeof import("../meetingNote")>()),
  createMeetingPage: vi.fn(async (id: string) => { s.mints.push(id); return { path: "kg/entities/meeting/2026-10-18-weekly-sync.md", workspace: "" }; }),
}));
vi.mock("../../data/workspaceApi", async (orig) => ({ ...(await orig<typeof import("../../data/workspaceApi")>()),
  readWorkspaceFile: vi.fn(async () => s.file),
  writeWorkspaceFile: vi.fn(async (path: string, content: string, opts?: { slug?: string }) => { s.writes.push({ path, content, slug: opts?.slug }); return { path, written: true }; }),
}));

import { MeetingPrep, type PrepNotes } from "../MeetingPrep";
import { readGoal } from "../prepRead";

const NOTE = "kg/entities/meeting/2026-10-18-weekly-sync.md";
const PAGE = ["---", "type: meeting", "meeting: 201", "---", "", "# Weekly sync", "", "## Agenda", "", "1. Test plan status", "",
  "Material: [[page:pilot/plans/test-plan.md|Test plan]]", ""].join("\n");
const ROWS = [
  { id: "200", title: "Weekly sync", scheduled_at: "2026-10-11T13:00:00Z", live_status: "completed" },
  { id: "201", title: "Weekly sync", scheduled_at: "2026-10-18T13:00:00Z", live_status: "scheduled",
    attendees: [{ email: "me@v.example" }, { email: "ana@x.example", name: "ana@x.example" }, { email: "carl@z.example" }] },
];

let container: HTMLDivElement;
let root: Root;
const reload = vi.fn();
const render = async (notes: PrepNotes) => { await act(async () => { root.render(<MeetingPrep meetingId="201" notes={notes} reload={reload} />); }); };
const text = () => container.textContent ?? "";

beforeEach(() => {
  s.rows = ROWS; s.writes = []; s.mints = []; s.file = "";
  s.people = [{ key: "a:7", subject: "7", name: "Ana Pereira", email: "ana@x.example", how: ["met", "chat"], headline: "Head of data", workspaces: [], roles: {}, vexa: true, last: null, page: null, presence: null }];
  s.cards = [
    { id: "c1", title: "Send the test plan", open: true, next: "Ana", about: { kind: "meeting", ref: "200" } },
    { id: "c2", title: "Closed", open: false, about: { kind: "meeting", ref: "200" } },
  ];
  reload.mockReset();
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

describe("Prep, read from the owners", () => {
  it("shows people with sources, last time's open cards, the agenda, the material and the ticks", async () => {
    await render({ phase: "ready", path: NOTE, workspace: "", body: PAGE });
    const t = text();
    expect(t).toContain("Ana Pereira");
    expect(t).toContain("Head of data");
    expect(t).toContain("met before");
    expect(t).toContain("chats");
    // nobody knows Carl yet: named from the invite, never a handle; me left out
    expect(t).toContain("Carl");
    expect(t).not.toContain("Me");
    expect(t).toContain("Send the test plan");
    expect(t).not.toContain("Closed");
    expect(t).toContain("Test plan status");
    expect(container.querySelector('[data-prep-material] [data-ref="[[page:pilot/plans/test-plan.md|Test plan]]"]')).not.toBeNull();
    expect(container.querySelector('[data-prep-tick="goal"]')?.hasAttribute("data-ok")).toBe(false);
    expect(container.querySelector('[data-prep-tick="material"]')?.hasAttribute("data-ok")).toBe(true);
    expect(t).toContain("only you");
    expect(t).toContain("Gathering");
  });

  it("says a goal on a shared workspace's page is shared, never 'only you'", async () => {
    await render({ phase: "ready", path: NOTE, workspace: "pilot", body: PAGE });
    expect(text()).toContain("on the shared page");
    expect(text()).not.toContain("only you");
  });

  it("Set goal writes goals: into the page it read, where it read it", async () => {
    await render({ phase: "ready", path: NOTE, workspace: "", body: PAGE });
    const input = container.querySelector("input") as HTMLInputElement;
    await act(async () => { fireEvent.change(input, { target: { value: "Agree the rollout date" } }); });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    expect(s.writes).toHaveLength(1);
    expect(s.writes[0].path).toBe(NOTE);
    expect(readGoal(s.writes[0].content)).toBe("Agree the rollout date");
    expect(s.writes[0].content.slice(s.writes[0].content.indexOf("# Weekly sync"))).toBe(PAGE.slice(PAGE.indexOf("# Weekly sync")));
    expect(reload).toHaveBeenCalled();
    expect(s.mints).toEqual([]);
  });

  it("a meeting with no page gets it minted first, then the goal written", async () => {
    s.file = "---\ntype: meeting\n---\n\n# Weekly sync\n";
    await render({ phase: "none" });
    const input = container.querySelector("input") as HTMLInputElement;
    await act(async () => { fireEvent.change(input, { target: { value: "Goal" } }); });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    expect(s.mints).toEqual(["201"]);
    expect(readGoal(s.writes[0].content)).toBe("Goal");
  });

  it("a set goal is shown, and the goal tick is on", async () => {
    await render({ phase: "ready", path: NOTE, workspace: "", body: PAGE.replace("meeting: 201", "meeting: 201\ngoals: Agree the date") });
    expect(text()).toContain("Agree the date");
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector('[data-prep-tick="goal"]')?.hasAttribute("data-ok")).toBe(true);
    expect(text()).toContain("Ready");
  });
});
