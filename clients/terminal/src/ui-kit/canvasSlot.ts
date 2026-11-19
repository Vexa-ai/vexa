/** THE MEETING CANVAS SLOT — how the meeting tab says "the canvas goes HERE" without importing it.
 *
 *  The meeting tab (`surfaces/meeting.tsx`) renders the one meeting view (`canvas/MeetingView`).
 *  The canvas reads the surfaces' meeting stores, so a static edge back from surfaces made the two
 *  folders import each other (Vexa-ai/vexa#1783). The canvas registers its view under this kind
 *  (`canvas/register.tsx`); the tab renders whatever is registered — the same seam as `transcriptSlot`.
 */

/** The kind the canvas view registers under (`contributions`). Its params carry `meetingId`. */
export const MEETING_CANVAS_VIEW_KIND = "meeting-canvas-view";

/** The canvas's horizontal content inset, which the meeting tab's header lines up with. */
export const MEETING_CANVAS_CONTENT_INSET = 18;
