"use client";
/** The canvas's registrations: the views the surfaces and the page renderer reach by kind, never by
 *  import. The composition root (`app/App.tsx`) imports this beside the surfaces barrel; the canvas
 *  sits above `surfaces/`, so the barrel cannot (Vexa-ai/vexa#1783). The "Open Meeting Canvas"
 *  command stays in `surfaces/canvas.tsx`, so its place in the palette does not move. */
import { registerTab } from "../contributions";
import { MeetingCanvasView } from "./MeetingCanvasView";
import { MeetingView } from "./MeetingView";
import { MeetingChip } from "./MeetingChip";
import { TRANSCRIPT_WIDGET_KIND } from "../ui-kit/transcriptSlot";
import { MEETING_CANVAS_VIEW_KIND } from "../ui-kit/canvasSlot";

function CanvasTab() {
  return <MeetingCanvasView />;
}

registerTab("canvas", CanvasTab);
// THE MEETING VIEW, INSIDE THE MEETING TAB: `surfaces/meeting.tsx` renders whatever is registered here.
registerTab(MEETING_CANVAS_VIEW_KIND, ({ params }) =>
  <MeetingView meetingId={params.meetingId as string} />);
// A PAGE THAT NAMES A MEETING (its marker or its frontmatter) shows ONE chip at its top that opens
// the meeting's view — the page does not embed the meeting (founder, UI lab 2026-10-14). `MdxDoc`
// renders whatever is registered under this kind, so the page renderer never imports this layer.
registerTab(TRANSCRIPT_WIDGET_KIND, ({ params }) =>
  <MeetingChip meetingId={String((params as { meetingId?: unknown })?.meetingId ?? "")} />);
