// @vitest-environment jsdom

import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/meeting-13627.json";
import { TranscriptViewer } from "@/components/transcript/transcript-viewer";
import { getPlatformConfig } from "@/types/vexa";
import type { Meeting, TranscriptSegment } from "@/types/vexa";

vi.mock("next/image", () => ({
  default: () => null,
}));

class QuietBoundary extends Component<
  { children: ReactNode; onError: (error: Error, info: ErrorInfo) => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError(error, info);
  }

  render() {
    return this.state.failed ? <p>This page couldn&apos;t load</p> : this.props.children;
  }
}

function meeting(status: Meeting["status"], platform: Meeting["platform"] = "jitsi"): Meeting {
  return {
    id: String(fixture.id),
    platform,
    platform_specific_id:
      platform === "google_meet" ? "abc-defg-hij" : fixture.native_meeting_id,
    status,
    start_time: fixture.start_time,
    end_time: status === "active" ? null : "2026-07-23T20:03:00Z",
    bot_container_id: null,
    data: fixture.data,
    created_at: fixture.start_time,
  };
}

const segments: TranscriptSegment[] = fixture.segments.map((segment) => ({
  id: segment.segment_id,
  meeting_id: String(fixture.id),
  start_time: segment.start,
  end_time: segment.end,
  absolute_start_time: segment.absolute_start_time,
  absolute_end_time: segment.absolute_end_time,
  text: segment.text,
  speaker: segment.speaker,
  language: segment.language,
  completed: true,
  session_uid: "sanitized-session",
  created_at: segment.created_at,
  segment_id: segment.segment_id,
}));

function renderSurface(
  status: Meeting["status"],
  renderedSegments = segments,
  platform: Meeting["platform"] = "jitsi"
) {
  const boundaryErrors: Error[] = [];
  const currentMeeting = meeting(status, platform);
  const platformConfig = getPlatformConfig(currentMeeting.platform);

  render(
    <QuietBoundary onError={(error) => boundaryErrors.push(error)}>
      <section aria-label="meeting detail transcript surface">
        <h1>{platformConfig.name}</h1>
        <TranscriptViewer
          meeting={currentMeeting}
          segments={renderedSegments}
          isLive={status === "active"}
        />
      </section>
    </QuietBoundary>
  );

  return boundaryErrors;
}

afterEach(cleanup);

describe("sanitized meeting 13627 detail rendering", () => {
  for (const status of ["active", "completed"] as const) {
    it(`renders every named Jitsi row while ${status} and keeps the boundary quiet`, () => {
      const boundaryErrors = renderSurface(status);

      expect(screen.getByRole("heading", { name: "Jitsi Meet" })).toBeTruthy();
      for (let row = 1; row <= 42; row += 1) {
        expect(screen.getByText(`Sanitized transcript row ${row}`)).toBeTruthy();
      }
      expect(screen.getAllByText("Anna").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Boris").length).toBeGreaterThan(0);
      expect(screen.queryByText("This page couldn't load")).toBeNull();
      expect(boundaryErrors).toEqual([]);
    });
  }

  it("preserves Google Meet confirmed and pending rows", () => {
    const gmeetSegments: TranscriptSegment[] = [
      {
        ...segments[0],
        id: "gmeet-confirmed",
        segment_id: "gmeet-confirmed",
        speaker: "GMeet Speaker",
        text: "GMeet confirmed control",
        completed: true,
      },
      {
        ...segments[1],
        id: "gmeet-pending",
        segment_id: "gmeet-pending",
        speaker: "GMeet Speaker",
        text: "GMeet pending control",
        completed: false,
      },
    ];

    const boundaryErrors = renderSurface("active", gmeetSegments, "google_meet");

    expect(screen.getByText("GMeet confirmed control")).toBeTruthy();
    expect(screen.getByText("GMeet pending control")).toBeTruthy();
    expect(screen.queryByText("This page couldn't load")).toBeNull();
    expect(boundaryErrors).toEqual([]);
  });
});
