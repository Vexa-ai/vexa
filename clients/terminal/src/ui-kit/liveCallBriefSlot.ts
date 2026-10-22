/** THE LIVE CALL BRIEF SLOT — how a chat's live call banner (`minutes/LiveCallBanner`) says "what Vexa
 *  listens for goes HERE" without importing the canvas, which sits above `minutes/`. The canvas
 *  registers the brief line under this kind (`canvas/register.tsx`); the banner renders whatever is
 *  registered — the same seam as `canvasSlot`.
 */

/** The kind the brief line registers under (`contributions`). Its params carry `meetingId` and
 *  `editable` (the reader owns the meeting). */
export const LIVE_CALL_BRIEF_KIND = "live-call-brief";
