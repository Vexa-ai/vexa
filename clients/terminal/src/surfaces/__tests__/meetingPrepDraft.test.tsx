/** Draft "+ Plan a meeting" — no empty backend row is created until the user fills something in.
 *  Clicking "+ Plan a meeting" opens a DRAFT prep tab; the planned-meeting row is created LAZILY on
 *  the first real input (here: the title blur), then the tab hands off to the canonical prep:<id>
 *  tab. Abandoning a draft leaves no empty "Untitled meeting" behind. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

const openTab = vi.fn();
const closeTab = vi.fn();
vi.mock("../../platform", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useService: () => ({ openTab, closeTab }),
}));
vi.mock("../liveMeetings", () => ({ useLiveMeetings: () => [], refreshMeetings: vi.fn() }));
vi.mock("../plannedApi", () => ({
  createPlannedMeeting: vi.fn(async () => ({ id: 77 })),
  updatePlannedMeeting: vi.fn(async () => ({})),
  deletePlannedMeeting: vi.fn(async () => {}),
}));
vi.mock("../workspaceApi", () => ({
  createSharedWorkspace: vi.fn(), listSharedMemberships: vi.fn(async () => []),
  listWorkspaceTree: vi.fn(async () => []), mintInvite: vi.fn(), readWorkspaceFile: vi.fn(async () => null),
}));
vi.mock("../briefNote", () => ({ findBriefNote: () => null, isExampleNote: () => false }));

import { registry } from "../../contributions";
import { prepDraftTabDescriptor, PREP_DRAFT_TAB_ID } from "../meetingPrep";
import * as planned from "../plannedApi";

const renderDraft = () => {
  const Comp = registry.tabComponent("meetingPrep")!;
  return render(<Comp id={PREP_DRAFT_TAB_ID} params={{ meetingId: "", draft: true }} active />);
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("meetingPrep draft — lazy row creation", () => {
  it("the draft descriptor carries no meetingId and a stable id", () => {
    expect(prepDraftTabDescriptor()).toMatchObject({ id: PREP_DRAFT_TAB_ID, kind: "meetingPrep", params: { meetingId: "", draft: true } });
  });

  it("opening a draft creates NO backend row", async () => {
    renderDraft();
    await screen.findByPlaceholderText("What's this meeting about?");
    expect(planned.createPlannedMeeting).not.toHaveBeenCalled();
  });

  it("the first real input (title blur) creates the row, then hands off to prep:<id>", async () => {
    renderDraft();
    const titleInput = await screen.findByPlaceholderText("What's this meeting about?");
    fireEvent.change(titleInput, { target: { value: "Acme sync" } });
    fireEvent.blur(titleInput);
    await waitFor(() => expect(planned.createPlannedMeeting).toHaveBeenCalledTimes(1));
    // created with the typed title (not an empty {})
    expect(planned.createPlannedMeeting).toHaveBeenCalledWith(expect.objectContaining({ title: "Acme sync" }));
    // hand off: the canonical prep tab opens, the draft closes
    await waitFor(() => expect(openTab).toHaveBeenCalledWith(expect.objectContaining({ id: "prep:77", kind: "meetingPrep" })));
    expect(closeTab).toHaveBeenCalledWith(PREP_DRAFT_TAB_ID);
  });

  it("blurring an empty title creates nothing (no phantom row)", async () => {
    renderDraft();
    const titleInput = await screen.findByPlaceholderText("What's this meeting about?");
    fireEvent.blur(titleInput);
    // an unchanged empty title is not a real input → no create
    await new Promise((r) => setTimeout(r, 0));
    expect(planned.createPlannedMeeting).not.toHaveBeenCalled();
  });
});
