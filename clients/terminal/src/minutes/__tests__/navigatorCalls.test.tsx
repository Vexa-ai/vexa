/** THE FILES | CALLS SWITCH, AS THE READER MEETS IT — inside the pages panel, through its own props.
 *
 *  Founder, 2026-10-11: *"we probably want to have a list of calls here that will open transcripts"*,
 *  then *"for list of calls let's have a switch files vs calls here"*.
 *
 *  The DENY rows are the point of this file: the Calls view lists only what the meeting list
 *  (`GET /api/meetings`, owned + shared) returned. A meeting that list does not carry — another
 *  person's call that was never shared — must never appear, never be reachable through the content
 *  search, and never be opened by a click.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import { NAV_OPEN_KEY } from "../navigatorApi";
import { NAV_VIEW_KEY } from "../NavCalls";
import { OPEN_PAGE_EVENT } from "../../platform";
import type { MeetingMock } from "../../surfaces/meetingModel";
import type { Page } from "../types";
import * as nav from "../navigatorApi";

const store = vi.hoisted(() => ({ meetings: [] as unknown[], loaded: true }));
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => store.meetings,
  useLiveMeetingsLoaded: () => store.loaded,
  applyMeetingTitle: () => {},
  refreshMeetings: () => {},
}));
vi.mock("../navigatorApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../navigatorApi")>()),
  loadNavWorkspaces: vi.fn(),
  loadNavTree: vi.fn(),
}));

const mtg = (o: Partial<MeetingMock> & { id: string }): MeetingMock => ({
  title: o.title_custom ?? "", when: "", status: "past", live_status: "completed", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...o,
});

/** What `GET /api/meetings` answered for THIS reader: two of their own, one shared with them, one
 *  plan (no transcript yet). Meeting 999 belongs to someone else and was never shared — so it is
 *  not here, which is the whole of the authorisation the panel relies on. */
const LIST: MeetingMock[] = [
  mtg({ id: "11", title_custom: "Acme pricing review", start_time: "2026-10-09T10:00:00Z", workspace_id: "acme-kg" }),
  mtg({ id: "12", native_id: "abc-defg-hij", start_time: "2026-10-10T09:00:00Z" }),
  mtg({ id: "13", title_custom: "Board prep", start_time: "2026-10-11T08:00:00Z", shared: true }),
  mtg({ id: "14", title_custom: "Live standup", start_time: "2026-10-11T09:30:00Z", live_status: "active", status: "live" }),
  mtg({ id: "15", title_custom: "Next week planning", live_status: "scheduled", scheduled_at: "2026-10-18T09:00:00Z" }),
];
const FOREIGN_ID = "999";

const WORKSPACES: nav.NavWorkspace[] = [
  { key: "desk", slug: undefined, name: "Dmitry's desk", kind: "desk" },
  { key: "acme-kg", slug: "acme-kg", name: "Acme", kind: "group" },
];

const PATH = "README.md";
const pages: Page[] = [{ path: PATH, label: "README" }];
const onOpen = vi.fn();
const opened: unknown[] = [];
const onOpenPage = (e: Event) => opened.push((e as CustomEvent).detail);

const panel = () => render(<PagesPanel pages={pages} docPath={PATH} onOpen={onOpen} body={"# Desk"} />);

async function openCalls() {
  localStorage.setItem(NAV_OPEN_KEY, "1");
  const r = panel();
  await screen.findByText("Dmitry's desk");
  fireEvent.click(r.container.querySelector('[data-nav-view="calls"]') as HTMLElement);
  return r.container;
}
const callIds = (c: HTMLElement) => [...c.querySelectorAll("[data-call]")].map((b) => b.getAttribute("data-call"));

beforeEach(() => {
  localStorage.clear();
  onOpen.mockClear();
  opened.length = 0;
  store.meetings = LIST;
  store.loaded = true;
  vi.mocked(nav.loadNavWorkspaces).mockReset().mockResolvedValue(WORKSPACES);
  vi.mocked(nav.loadNavTree).mockReset().mockResolvedValue(["README.md"]);
  window.addEventListener(OPEN_PAGE_EVENT, onOpenPage);
});
afterEach(() => { cleanup(); window.removeEventListener(OPEN_PAGE_EVENT, onOpenPage); vi.unstubAllGlobals(); });

describe("the switch", () => {
  it("opens on Files with the tree unchanged, and Calls replaces it", async () => {
    localStorage.setItem(NAV_OPEN_KEY, "1");
    const { container } = panel();
    await screen.findByText("Dmitry's desk");
    const files = container.querySelector('[data-nav-view="files"]') as HTMLElement;
    expect(files.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("[data-calls]")).toBeNull();
    fireEvent.click(container.querySelector('[data-nav-view="calls"]') as HTMLElement);
    expect(container.querySelector("[data-calls]")).toBeTruthy();
    expect(container.querySelector("[data-nav-ws]")).toBeNull();
    expect((container.querySelector("[data-nav-filter]") as HTMLInputElement).placeholder).toMatch(/^Filter calls/);
  });

  it("remembers the last choice per browser", async () => {
    await openCalls();
    expect(localStorage.getItem(NAV_VIEW_KEY)).toBe("calls");
    cleanup();
    const again = panel();
    await waitFor(() => expect(again.container.querySelector("[data-calls]")).toBeTruthy());
  });

  it("is a keyboard tablist: arrows move and select, only the selected tab is tabbable", async () => {
    localStorage.setItem(NAV_OPEN_KEY, "1");
    const { container } = panel();
    await screen.findByText("Dmitry's desk");
    const list = container.querySelector("[role=tablist]") as HTMLElement;
    const tabs = [...list.querySelectorAll("[role=tab]")] as HTMLElement[];
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1]);
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(container.querySelector("[data-calls]")).toBeTruthy();
    expect(document.activeElement).toBe(container.querySelector('[data-nav-view="calls"]'));
    fireEvent.keyDown(list, { key: "Home" });
    expect(container.querySelector("[data-calls]")).toBeNull();
  });
});

describe("the list", () => {
  it("lists calls with a transcript, live first, then newest first; a plan is not a call", async () => {
    const c = await openCalls();
    expect(callIds(c)).toEqual(["14", "13", "12", "11"]);
    expect(c.querySelector('[data-calls-group="live"]')).toBeTruthy();
    expect(c.querySelector('[data-call="14"] [data-call-live]')).toBeTruthy();
  });

  it("labels an untitled call by who was in it — never its platform id (UI decisions 14)", async () => {
    const c = await openCalls();
    expect(c.querySelector('[data-call="12"]')?.textContent).toContain("Call with Vexa");
    expect(c.querySelector('[data-call="12"]')?.textContent).not.toContain("abc-defg-hij");
  });

  it("a click opens the transcript through the shell's open-page route and mints no tab", async () => {
    const c = await openCalls();
    fireEvent.click(c.querySelector('[data-call="11"]') as HTMLElement);
    expect(opened).toEqual([{ path: "meeting:11", label: "Acme pricing review" }]);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("⧉ keeps it in a tab as a meeting page", async () => {
    const c = await openCalls();
    fireEvent.click(c.querySelector('[data-call-tab="13"]') as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith({ kind: "meeting", path: "13", label: "Board prep" });
  });

  it("pages a long list: Show more reveals the next page", async () => {
    store.meetings = Array.from({ length: 70 }, (_, i) =>
      mtg({ id: String(100 + i), title_custom: `Call ${i}`, start_time: new Date(Date.UTC(2026, 9, 1) - i * 864e5).toISOString() }));
    const c = await openCalls();
    expect(callIds(c).length).toBe(30);
    fireEvent.click(c.querySelector("[data-calls-more]") as HTMLElement);
    expect(callIds(c).length).toBe(60);
    // more than 40 → grouped by month
    expect([...c.querySelectorAll("[data-calls-group]")].every((g) => g.getAttribute("data-calls-group")!.startsWith("m:"))).toBe(true);
  });

  it("filters by title", async () => {
    const c = await openCalls();
    fireEvent.change(c.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "board" } });
    expect(callIds(c)).toEqual(["13"]);
    fireEvent.change(c.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "zzz" } });
    expect(c.querySelector("[data-calls-empty]")).toBeTruthy();
  });
});

describe("DENY: another person's unshared meeting never appears or opens", () => {
  it("is not listed — the list is exactly what the meeting list returned", async () => {
    const c = await openCalls();
    expect(callIds(c)).not.toContain(FOREIGN_ID);
    fireEvent.change(c.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "secret" } });
    expect(callIds(c)).toEqual([]);
  });

  it("a content-search hit for it is dropped, so it can never be clicked open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ hits: [
        { meeting_db_id: 999, snippet: "the secret merger" },
        { meeting_db_id: 11, snippet: "pricing for the merger" },
      ] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const c = await openCalls();
    fireEvent.change(c.querySelector("[data-nav-filter]") as HTMLInputElement, { target: { value: "> merger" } });
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(callIds(c)).toEqual(["11"]));
    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(urls).toContain("/api/transcripts/search?q=merger&limit=50");
    expect(urls.filter((u) => u.includes("meeting") || u.includes(FOREIGN_ID))).toEqual([]);   // no other read path
    expect(c.textContent).not.toContain("secret merger");
    fireEvent.click(c.querySelector('[data-call="11"]') as HTMLElement);
    expect(opened).toEqual([{ path: "meeting:11", label: "Acme pricing review" }]);
    vi.useRealTimers();
  });
});

describe("the meeting ↔ workspace link", () => {
  it("a call bound to a listed workspace shows it, and the chip opens that workspace in Files", async () => {
    const c = await openCalls();
    const chip = c.querySelector('[data-call="11"]')!.parentElement!.querySelector('[data-call-ws="acme-kg"]') as HTMLElement;
    expect(chip).toBeTruthy();
    expect(c.querySelector('[data-call="12"]')!.parentElement!.querySelector("[data-call-ws]")).toBeNull();
    fireEvent.click(chip);
    expect(c.querySelector("[data-calls]")).toBeNull();
    expect(c.querySelector('[data-nav-ws="acme-kg"]')?.getAttribute("aria-expanded")).toBe("true");
  });

  it("the workspace row counts its calls, and the count lists them", async () => {
    localStorage.setItem(NAV_OPEN_KEY, "1");
    const { container } = panel();
    await screen.findByText("Dmitry's desk");
    const badge = container.querySelector('[data-nav-ws-calls="acme-kg"]') as HTMLElement;
    expect(badge.textContent).toBe("1");
    expect(container.querySelector('[data-nav-ws-calls="desk"]')).toBeNull();
    fireEvent.click(badge);
    expect(callIds(container)).toEqual(["11"]);
    expect(container.querySelector("[data-calls-ws-filter]")?.textContent).toContain("Acme");
  });

  it("a workspace the reader cannot see yields no chip", async () => {
    store.meetings = [mtg({ id: "21", title_custom: "Elsewhere", start_time: "2026-10-01T00:00:00Z", workspace_id: "not-mine" })];
    const c = await openCalls();
    expect(c.querySelector("[data-call-ws]")).toBeNull();
  });
});

describe("rename a call from its row", () => {
  const annotateCalls = (f: ReturnType<typeof vi.fn>) =>
    f.mock.calls.filter((c) => String((c as unknown[])[0]).endsWith("/annotate")).map((c) => [String((c as unknown[])[0]), JSON.parse(((c as unknown[])[1] as RequestInit).body as string)]);

  it("⋯ → Rename edits the row in place; Enter writes the meeting's title through annotate", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);
    const c = await openCalls();
    fireEvent.click(c.querySelector('[data-call-more="12"]') as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: /Rename/ }));
    const input = c.querySelector('[data-call-rename="12"] input') as HTMLInputElement;
    expect(input.value).toBe("Call with Vexa");
    fireEvent.change(input, { target: { value: "Acme" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(annotateCalls(fetchMock)).toEqual([["/api/meetings/12/annotate", { title: "Acme" }]]));
    await waitFor(() => expect(c.querySelector("[data-call-rename]")).toBeNull());
  });

  it("F2 on a focused row starts the rename; Escape puts the row back untouched", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    vi.stubGlobal("fetch", fetchMock);
    const c = await openCalls();
    fireEvent.keyDown(c.querySelector('[data-call="11"]') as HTMLElement, { key: "F2" });
    const input = c.querySelector('[data-call-rename="11"] input') as HTMLInputElement;
    expect(input.value).toBe("Acme pricing review");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(c.querySelector("[data-call-rename]")).toBeNull();
    expect(c.querySelector('[data-call="11"]')).toBeTruthy();
    expect(annotateCalls(fetchMock)).toEqual([]);
  });

  it("DENY: a call shared with you offers no rename — no ⋯, and F2 does nothing", async () => {
    const c = await openCalls();
    expect(c.querySelector('[data-call-more="13"]')).toBeNull();
    fireEvent.keyDown(c.querySelector('[data-call="13"]') as HTMLElement, { key: "F2" });
    expect(c.querySelector("[data-call-rename]")).toBeNull();
  });
});

describe("deleted calls and the row's own layout", () => {
  it("a call whose transcript was deleted is not listed", async () => {
    store.meetings = [...LIST, mtg({ id: "16", native_id: "rhw-abcd-efg", start_time: "2026-10-10T21:09:00Z", artifacts_deleted: true })];
    const c = await openCalls();
    expect(callIds(c)).not.toContain("16");
    expect(callIds(c)).toEqual(["14", "13", "12", "11"]);
  });

  it("under the pointer the row's actions take the time's place instead of drawing over it", async () => {
    const c = await openCalls();
    const row = c.querySelector('[data-call-row="12"]') as HTMLElement;
    const when = row.querySelector("[data-call-when]") as HTMLElement;
    const acts = row.querySelector("[data-call-acts]") as HTMLElement;
    expect(when.style.visibility).not.toBe("hidden");
    expect(acts.style.opacity).toBe("0");
    fireEvent.mouseEnter(row);
    expect(when.style.visibility).toBe("hidden");
    expect(acts.style.opacity).toBe("1");
    fireEvent.mouseLeave(row);
    expect(when.style.visibility).toBe("visible");
    expect(acts.style.opacity).toBe("0");
  });

  it("a call that never started is listed at its request time and says so", async () => {
    store.meetings = [mtg({ id: "17", title_custom: "Acme", live_status: "failed", created_at: "2026-10-11T07:00:00Z" })];
    const c = await openCalls();
    expect(c.querySelector('[data-calls-group="undated"]')).toBeNull();
    expect(c.querySelector('[data-call="17"]')?.getAttribute("aria-label")).toMatch(/never started/);
    expect(c.querySelector('[data-call="17"] [data-call-not-started]')).toBeTruthy();
  });
});
