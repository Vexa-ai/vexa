import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [{ id: "42", native_id: "n-1", platform: "Zoom", status: "past", live_status: "completed", start_time: "2026-10-09T14:00:00Z", participants: [] }],
  useLiveMeetingsConnection: () => true, refreshMeetings: vi.fn(),
}));
vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ recordings: [] }))));
import { MeetingPageHeader } from "../MeetingPageHeader";
afterEach(cleanup);
it("puts the title and platform · date · status on one row, and leaves Delete to the header group", () => {
  const { container } = render(<MeetingPageHeader meetingId="42" body="# Weekly sync" path="meetings/x.md" />);
  const title = container.querySelector("[data-doc-name]")!;
  const meta = container.querySelector("[data-meeting-metadata]")!;
  expect(title.textContent).toBe("Weekly sync");
  expect(meta.parentElement).toBe(title.parentElement);
  expect((meta as HTMLElement).style.whiteSpace).toBe("nowrap");
  expect(meta.getAttribute("title")).toContain("Zoom");
  expect(screen.queryByRole("button", { name: "Delete meeting data" })).toBeNull();
});
