import { describe, it, expect } from "vitest";
import { firstViewPlan } from "../firstView";

/** The landing priority: what the user sees on first view, by what's SHARED with them. */
describe("firstViewPlan — landing priority resolution", () => {
  const base = { sharedMeetingId: null, routeMeetingId: null, acceptedSlug: null, sharedSlug: null, liveMeetingId: null, fresh: true } as const;

  it("nothing shared, fresh dock → the Meetings day (Today)", () => {
    expect(firstViewPlan({ ...base })).toEqual({ kind: "own-day" });
  });

  it("a PASSIVELY-mounted shared workspace (no meeting, no accept) does NOT hijack landing → the Meetings day", () => {
    expect(firstViewPlan({ ...base, sharedSlug: "deal-ab12" })).toEqual({ kind: "own-day" });
  });

  it("a shared meeting (no workspace) → the meeting is the view", () => {
    expect(firstViewPlan({ ...base, sharedMeetingId: "m7" })).toEqual({ kind: "meeting", meetingId: "m7" });
  });

  it("a shared meeting AND a shared workspace → workspace README + the meeting (its live badge shows)", () => {
    expect(firstViewPlan({ ...base, sharedMeetingId: "m7", sharedSlug: "deal-ab12" }))
      .toEqual({ kind: "meeting-and-workspace", meetingId: "m7", slug: "deal-ab12" });
  });

  it("nothing shared but a live meeting is already known → open that live meeting", () => {
    expect(firstViewPlan({ ...base, liveMeetingId: "live9" })).toEqual({ kind: "live-meeting", meetingId: "live9" });
  });

  it("an explicit shared meeting outranks a known live meeting", () => {
    expect(firstViewPlan({ ...base, sharedMeetingId: "m7", liveMeetingId: "live9" })).toEqual({ kind: "meeting", meetingId: "m7" });
  });

  describe("an ADDRESSED meeting (`/meetings/<id>` — the durable reference)", () => {
    it("the URL's meeting is the view", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: "482" })).toEqual({ kind: "meeting", meetingId: "482" });
    });

    it("outranks a known live meeting (the address bar said which one)", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: "482", liveMeetingId: "live9" })).toEqual({ kind: "meeting", meetingId: "482" });
    });

    it("a just-redeemed share link still outranks the URL", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: "482", sharedMeetingId: "m7" })).toEqual({ kind: "meeting", meetingId: "m7" });
    });

    it("a passively-mounted shared workspace does NOT ride along — the address is the meeting", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: "482", sharedSlug: "deal-ab12" })).toEqual({ kind: "meeting", meetingId: "482" });
    });

    it("a JUST-ACCEPTED invite does ride along (both were explicit acts)", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: "482", acceptedSlug: "deal-ab12" }))
        .toEqual({ kind: "meeting-and-workspace", meetingId: "482", slug: "deal-ab12" });
    });

    it("applies to a RETURNING user — a reloaded meeting URL restores that meeting, not the saved layout", () => {
      expect(firstViewPlan({ ...base, fresh: false, routeMeetingId: "482" })).toEqual({ kind: "meeting", meetingId: "482" });
    });

    it("no URL id → landing is unchanged (the day)", () => {
      expect(firstViewPlan({ ...base, routeMeetingId: null })).toEqual({ kind: "own-day" });
    });
  });

  describe("a returning user (dock restored tabs — not fresh)", () => {
    const returning = { ...base, fresh: false } as const;

    it("with nothing shared → noop (their saved layout is left alone)", () => {
      expect(firstViewPlan({ ...returning })).toEqual({ kind: "noop" });
    });

    it("with a shared workspace but no explicit meeting → still noop (no surprise re-pin)", () => {
      expect(firstViewPlan({ ...returning, sharedSlug: "deal-ab12" })).toEqual({ kind: "noop" });
    });

    it("with a live meeting but no explicit share → still noop", () => {
      expect(firstViewPlan({ ...returning, liveMeetingId: "live9" })).toEqual({ kind: "noop" });
    });

    it("but an EXPLICIT shared meeting still applies (they clicked a share link)", () => {
      expect(firstViewPlan({ ...returning, sharedMeetingId: "m7" })).toEqual({ kind: "meeting", meetingId: "m7" });
    });

    it("an explicit shared meeting + a shared workspace applies even when not fresh", () => {
      expect(firstViewPlan({ ...returning, sharedMeetingId: "m7", sharedSlug: "deal-ab12" }))
        .toEqual({ kind: "meeting-and-workspace", meetingId: "m7", slug: "deal-ab12" });
    });

    it("a JUST-ACCEPTED invite pins the shared workspace README even when not fresh", () => {
      expect(firstViewPlan({ ...returning, acceptedSlug: "deal-ab12" }))
        .toEqual({ kind: "workspace-readme", slug: "deal-ab12" });
    });

    it("an accepted invite outranks the passive active-set sharedSlug", () => {
      expect(firstViewPlan({ ...returning, acceptedSlug: "deal-ab12", sharedSlug: "other-99" }))
        .toEqual({ kind: "workspace-readme", slug: "deal-ab12" });
    });

    it("an accepted invite + an accepted shared meeting → both (README uses the accepted slug)", () => {
      expect(firstViewPlan({ ...returning, sharedMeetingId: "m7", acceptedSlug: "deal-ab12" }))
        .toEqual({ kind: "meeting-and-workspace", meetingId: "m7", slug: "deal-ab12" });
    });
  });
});
