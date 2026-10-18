"use client";
import { registerCommand, registerTab } from "../contributions";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { MeetingCanvasView } from "../canvas/MeetingCanvasView";
import { MeetingTranscriptWidget } from "../canvas/TranscriptWidget";
import { TRANSCRIPT_WIDGET_KIND } from "../ui-kit/transcriptSlot";

function canvasTab(): TabDescriptor {
  return { id: "meeting-canvas", title: "Meeting Canvas", kind: "canvas", params: {}, context: null };
}

function CanvasTab() {
  return <MeetingCanvasView />;
}

registerTab("canvas", CanvasTab);
// THE SAME TRANSCRIPT, INSIDE A PAGE (Vexa-ai/vexa#1598). A meeting doc declares the widget with an
// HTML-comment marker and `MdxDoc` renders whatever is registered under this kind — so the page
// renderer never imports the meeting layer, and a build without this surface says so in one line
// instead of printing the marker at the reader.
registerTab(TRANSCRIPT_WIDGET_KIND, ({ params }) =>
  <MeetingTranscriptWidget meetingId={String((params as { meetingId?: unknown })?.meetingId ?? "")} />);
registerCommand({
  id: "meeting.canvas.open",
  title: "Open Meeting Canvas",
  run: ({ container }) => container.get(LayoutServiceId).openTab(canvasTab()),
});
