import { describe, expect, it } from "vitest";

import { pinSubscriptionUid } from "../useMeeting";

/** ADR-0027 / P21 — the live→durable handoff at bot stop. The list row's live flag is INTENT (it
 *  clears the instant the FSM flips); the stream's own `meeting-end` is the EVIDENCE the
 *  subscription releases on. The pin keeps the subscription key stable across the flip so the live
 *  notes stay in view while the copilot's final beat is still arriving — the "processed notes
 *  disappear on stop" defect was exactly this key changing mid-handoff. */
describe("pinSubscriptionUid (the stop-transition pin)", () => {
  const EMPTY = { id: "", uid: "" };

  it("pins the uid while the row is live", () => {
    expect(pinSubscriptionUid(EMPTY, "51", "51")).toEqual({ id: "51", uid: "51" });
  });

  it("HOLDS the pin when session_uid clears on the SAME meeting (the stop flip)", () => {
    const pinned = pinSubscriptionUid(EMPTY, "51", "51");
    // the FSM flipped terminal → the row's session_uid is gone; the subscription must not move
    expect(pinSubscriptionUid(pinned, "51", undefined)).toBe(pinned);
  });

  it("drops the pin when a DIFFERENT meeting is selected (no cross-meeting subscription)", () => {
    const pinned = pinSubscriptionUid(EMPTY, "51", "51");
    expect(pinSubscriptionUid(pinned, "52", undefined)).toEqual(EMPTY);
  });

  it("re-pins when the newly selected meeting is itself live", () => {
    const pinned = pinSubscriptionUid(EMPTY, "51", "51");
    expect(pinSubscriptionUid(pinned, "53", "53")).toEqual({ id: "53", uid: "53" });
  });

  it("never invents a subscription for a meeting that was never live this session", () => {
    expect(pinSubscriptionUid(EMPTY, "46", undefined)).toEqual(EMPTY);
  });
});
