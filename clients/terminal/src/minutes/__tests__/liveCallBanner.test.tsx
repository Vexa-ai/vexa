/** A chat listens to a live call: the banner at the top of a chat offers "Listen here" for a call that
 *  wakes another chat, hides on "Not now" for this chat and call, shows the brief and — for the owner
 *  only — the two opt-ins when this chat is the one, and is absent for a call the reader cannot read. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { MeetingMock } from "../../surfaces/meetingModel";

const PAGE = ["---", "title: Weekly", "listen:", "  focus: Install dates", "  labels:",
  "    - {name: addressed_to_vexa, wake: true}", "  version: 1", "---", "", "# Weekly", ""].join("\n");

vi.mock("../../canvas/MeetingView", () => ({
  useMeetingNotes: () => ({ phase: "ready", path: "meetings/weekly.md", workspace: "", body: PAGE, cursor: null, updatedAt: null, reload: () => {} }),
}));

import { LiveCallBanner, LiveCallBanners } from "../LiveCallBanner";
import "../../canvas/register";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const MEETING = { id: "207", title: "Call with Vu", when: "", status: "live", live_status: "active", platform: "google_meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], start_time: "2026-10-22T09:00:00Z" } as unknown as MeetingMock;

const att = (over: Record<string, unknown> = {}) => ({ meeting: "207", attached: true, attached_here: false, chat: { session: "pchat-prep", title: "Prep for 10:15" },
  mine: true, how: "auto", speak: false, post_chat: false, owner: true, ...over });

type Reply = { status: number; body?: unknown };
let replies: Reply[]; let calls: { url: string; init?: RequestInit }[];
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  const r = replies.length > 1 ? replies.shift()! : replies[0];
  return { ok: r.status < 400, status: r.status, headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => r.body ?? {}, text: async () => JSON.stringify(r.body ?? {}) } as unknown as Response;
});

let container: HTMLDivElement; let root: Root;
beforeEach(() => {
  replies = []; calls = []; fetchMock.mockClear();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  try { localStorage.clear(); } catch { /* none */ }
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await Promise.resolve(); }); };
const mount = async (ui: React.ReactElement) => { act(() => { root.render(ui); }); await flush(); };
const q = (sel: string) => container.querySelector(sel) as HTMLElement | null;

describe("LiveCallBanner", () => {
  it("a call that wakes another chat offers Listen here, and moves the attachment on click", async () => {
    replies = [{ status: 200, body: att() }, { status: 200, body: att({ attached_here: true, how: "chose" }) }, { status: 200, body: att({ attached_here: true, how: "chose" }) }];
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    expect(calls[0].url).toBe("/api/meeting/207/attachment?session=pchat-1");
    const line = q('[data-live-call="207"]')!;
    expect(line.dataset.attached).toBe("elsewhere");
    expect(line.textContent).toContain("Live call: Call with Vu");
    expect(line.textContent).toContain("listening in Prep for 10:15");
    await act(async () => { q("[data-live-call-listen]")!.click(); });
    await flush();
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toBe("/api/meeting/207/attachment");
    expect(post.init!.body).toBe(JSON.stringify({ session: "pchat-1" }));
    expect(q('[data-live-call="207"]')!.dataset.attached).toBe("here");
  });

  it("Not now hides it for this chat and call, and stays hidden after a remount", async () => {
    replies = [{ status: 200, body: att() }];
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    act(() => { q("[data-live-call-not-now]")!.click(); });
    expect(q("[data-live-call]")).toBeNull();
    act(() => root.unmount()); root = createRoot(container);
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    expect(q("[data-live-call]")).toBeNull();
    // another chat still offers it
    act(() => root.unmount()); root = createRoot(container);
    await mount(<LiveCallBanner session="pchat-2" meeting={MEETING} pollMs={0} />);
    expect(q('[data-live-call="207"]')).not.toBeNull();
  });

  it("the attached chat shows what Vexa listens for and, for the owner, the two opt-ins", async () => {
    replies = [{ status: 200, body: att({ attached_here: true }) }, { status: 200, body: att({ attached_here: true, speak: true }) }];
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    expect(q('[data-live-call="207"]')!.textContent).toContain("Listening to Call with Vu");
    expect(q("[data-listen-line]")!.textContent).toContain("Install dates");
    const speak = q("[data-live-call-speak]") as HTMLInputElement;
    expect(speak.checked).toBe(false);
    expect(q("[data-live-call-post]")).not.toBeNull();
    await act(async () => { speak.click(); });
    await flush();
    const patch = calls.find((c) => c.init?.method === "PATCH")!;
    expect(patch.url).toBe("/api/meeting/207/attachment");
    expect(patch.init!.body).toBe(JSON.stringify({ speak: true }));
    expect((q("[data-live-call-speak]") as HTMLInputElement).checked).toBe(true);
  });

  it("a reader who does not own the meeting gets no opt-ins", async () => {
    replies = [{ status: 200, body: att({ attached_here: true, owner: false }) }];
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    expect(q('[data-live-call="207"]')).not.toBeNull();
    expect(q("[data-live-call-optins]")).toBeNull();
  });

  it("a call the reader cannot read (404) has no line", async () => {
    replies = [{ status: 404, body: { detail: "not found" } }];
    await mount(<LiveCallBanner session="pchat-1" meeting={MEETING} pollMs={0} />);
    expect(calls).toHaveLength(1);
    expect(container.innerHTML).toBe("");
  });

  it("no line in a meeting's own chat, nor for a call that is not live", async () => {
    replies = [{ status: 200, body: att() }];
    await mount(<LiveCallBanners session="meet-207" meetings={[MEETING]} />);
    await mount(<LiveCallBanners session="pchat-1" meetings={[{ ...MEETING, live_status: "completed", status: "past" } as MeetingMock]} />);
    expect(calls).toHaveLength(0);
    expect(container.innerHTML).toBe("");
  });
});

describe("the attachment's wire shape", () => {
  it("names the chat by its title, never by its id", async () => {
    const { fromWire } = await import("../../data/meetingAttachment");
    expect(fromWire({ meeting: "210", attached: true, attached_here: false, mine: true, how: "auto", speak: false,
      post_chat: false, owner: true, chat: { session: "pchat-mvjitvmi", title: "pchat-mvjitvmi" } }).chat_title).toBeUndefined();
    expect(fromWire({ meeting: "210", attached: true, attached_here: false, mine: true, how: "sent", speak: false,
      post_chat: false, owner: true, chat: { session: "pchat-a", title: "Prep" } })).toMatchObject({ row: "210", chat_title: "Prep" });
  });
});
