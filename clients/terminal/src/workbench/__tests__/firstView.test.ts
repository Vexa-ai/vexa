import { describe, it, expect } from "vitest";
import { firstViewPlan } from "../firstView";

/** The landing priority: what the user sees on first view, by what's SHARED with them. */
describe("firstViewPlan — landing priority resolution", () => {
  const base = { sharedMeetingId: null, acceptedSlug: null, sharedSlug: null, liveMeetingId: null, fresh: true } as const;

  it("nothing shared, fresh dock → the user's own README-onboarding", () => {
    expect(firstViewPlan({ ...base })).toEqual({ kind: "own-readme" });
  });

  it("a shared workspace (no meeting) → that workspace's README pinned", () => {
    expect(firstViewPlan({ ...base, sharedSlug: "deal-ab12" })).toEqual({ kind: "workspace-readme", slug: "deal-ab12" });
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
