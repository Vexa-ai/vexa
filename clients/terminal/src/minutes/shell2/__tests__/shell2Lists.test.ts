/** UI-1 (feature `shell2`): the Inbox and Meetings decisions, pure. */
import { describe, expect, it } from "vitest";
import { inboxCount, inboxGroups, inboxNeeds } from "../inbox";
import { groupPast, matchMeetings, pastMeetings, peopleOf, upcomingMeetings, weekMonthGroup, NO_FILTERS } from "../meetings";
import { canDecline } from "../needs";
import type { Need } from "../../desktop/home2/attentionApi";
import type { MeetingMock } from "../../../surfaces/meetingModel";

const need = (id: string, kind: Need["kind"], workspace = "w1", act: Partial<Need["act"]> = {}): Need =>
  ({ id, kind, workspace, text: `Item ${id}`, at: Number(id.replace(/\D/g, "")) || 1, act: { label: "Open", kind: "open-page", ...act } as Need["act"] });

describe("the Inbox (one read model)", () => {
  it("groups Waiting on you · Mentions · Did not run · Coming up and counts everything but the upcoming", () => {
    const ns = [need("n1", "proposal"), need("n2", "mention"), need("n3", "upcoming"), need("n4", "card"), need("n5", "failed")];
    expect(inboxGroups(ns).map((g) => [g.key, g.needs.length])).toEqual([["waiting", 2], ["mentions", 1], ["failed", 1], ["upcoming", 1]]);
    expect(inboxCount(ns)).toBe(4);
  });
  it("leaves out muted or hidden workspaces", () => {
    expect(inboxNeeds([need("n1", "mention", "quiet"), need("n2", "mention")], (s) => s === "quiet").map((n) => n.id)).toEqual(["n2"]);
  });
  it("offers Decline only on a proposal decided here (R11: never on a mention, a card or a call's proposal)", () => {
    expect(canDecline(need("n1", "proposal", "w", { kind: "accept-proposal", proposal: "p1" }))).toBe(true);
    expect(canDecline(need("n2", "proposal", "w", { kind: "accept-proposal", proposal: "p1", source: "call" }))).toBe(false);
    expect(canDecline(need("n3", "mention"))).toBe(false);
  });
});

const m = (id: string, extra: Partial<MeetingMock>): MeetingMock => ({ id, title: `Meeting ${id}`, when: "", status: "past", platform: "google_meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...extra }) as MeetingMock;

describe("the Meetings page", () => {
  const now = new Date("2026-10-17T12:00:00Z").getTime();
  it("upcoming is planned and due, soonest first; past is newest first and never deleted", () => {
    const ms = [m("1", { live_status: "scheduled", scheduled_at: "2026-10-19T09:00:00Z" }), m("2", { live_status: "scheduled", scheduled_at: "2026-10-18T09:00:00Z" }),
      m("3", { live_status: "completed", start_time: "2026-10-10T09:00:00Z" }), m("4", { live_status: "completed", start_time: "2026-10-16T09:00:00Z" }),
      m("5", { live_status: "completed", start_time: "2026-10-16T10:00:00Z", artifacts_deleted: true })];
    expect(upcomingMeetings(ms, now).map((x) => x.id)).toEqual(["2", "1"]);
    expect(pastMeetings(ms).map((x) => x.id)).toEqual(["4", "3"]);
  });
  it("groups by week, then month", () => {
    const n = new Date("2026-10-17T12:00:00");
    expect(weekMonthGroup("2026-10-16T09:00:00", n).label).toBe("This week");
    expect(weekMonthGroup("2026-10-08T09:00:00", n).label).toBe("Last week");
    expect(weekMonthGroup("2026-10-01T09:00:00", n).label).toBe("October");
    expect(weekMonthGroup("2025-09-01T09:00:00", n).label).toBe("September 2025");
    const g = groupPast([m("a", { start_time: "2026-10-16T09:00:00" }), m("b", { start_time: "2026-10-15T09:00:00" }), m("c", { start_time: "2026-10-01T09:00:00" })], n);
    expect(g.map((x) => [x.label, x.rows.length])).toEqual([["This week", 2], ["October", 1]]);
  });
  it("searches title, people and workspace; filters by workspace and person; people are identified humans only", () => {
    const ms = [m("1", { title: "Pricing review", workspace_id: "w1", participants: [{ name: "Ana Ortiz", role: "", initials: "" }, { name: "Speaker 2", role: "", initials: "" }] }),
      m("2", { title: "Seed swap", workspace_id: "w2", participants: [{ name: "Ben Kim", role: "", initials: "" }] })];
    const name = (s: string) => (s === "w1" ? "Product" : "Garden");
    expect(peopleOf(ms[0])).toEqual(["Ana Ortiz"]);
    expect(matchMeetings(ms, { ...NO_FILTERS, q: "garden" }, name).map((x) => x.id)).toEqual(["2"]);
    expect(matchMeetings(ms, { ...NO_FILTERS, workspace: "w1" }, name).map((x) => x.id)).toEqual(["1"]);
    expect(matchMeetings(ms, { ...NO_FILTERS, person: "Ben Kim" }, name).map((x) => x.id)).toEqual(["2"]);
  });
});
