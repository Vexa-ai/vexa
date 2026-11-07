/** A SHARE ARRIVAL LANDS ON THE MEETING, not on the reader's desk.
 *
 *  Found live on app.dev: an invitee followed a mailed share link, the redeem worked, the meeting's
 *  chat opened — and the pages panel showed their own desk README, with the meeting a tab away and
 *  nothing saying it had been shared with them. The rule is `sharedArrivalFront`; these pin it over
 *  the pages the room itself composes (`pagesForPhase`), so a change to the room's layout is caught
 *  here rather than on somebody's first visit. */
import { describe, it, expect } from "vitest";
import { pagesForPhase, sharedArrivalFront } from "../roomView";
import type { Page } from "../types";

const desk: Page = { path: "README.md", label: "Desk", desk: true };

describe("sharedArrivalFront", () => {
  it("a recipient with no note of their own lands on the transcript canvas, not the desk", () => {
    const pages = [desk, ...pagesForPhase("post", "abc-defg-hij", "13", null)];
    const front = sharedArrivalFront(pages, "meeting");
    expect(front?.kind).toBe("meeting");
    expect(front?.path).toBe("13");
  });

  it("a room whose note renders the transcript lands on that note — the meeting's own page", () => {
    const pages = [desk, ...pagesForPhase("post", "abc-defg-hij", "13", "kg/entities/meeting/2026-10-08-x.md",
      { noteHasTranscript: true })];
    const front = sharedArrivalFront(pages, "meeting");
    expect(front?.permanent).toBe(true);
    expect(front?.path).not.toBe("README.md");
  });

  it("is no opinion at all unless this is a share arrival, so every other open is unchanged", () => {
    const pages = [desk, ...pagesForPhase("post", "abc-defg-hij", "13", null)];
    expect(sharedArrivalFront(pages)).toBeNull();
    expect(sharedArrivalFront([desk], "meeting")).toBeNull();
  });
});
