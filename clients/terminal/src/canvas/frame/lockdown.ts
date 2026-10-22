/** What the Meeting Canvas frame takes away before any view code runs (S03T-1).
 *
 *  The frame holds data the reader was shown, and a view is workspace-written code. Its policy
 *  (`canvasFrameContentSecurityPolicy`) closes every fetch, and the sandbox closes popups, forms and
 *  top-level navigation. Two ways out are left to close or bound:
 *
 *  - WEBRTC. ICE candidate gathering reaches a STUN/TURN host of the view's choosing, and no CSP
 *    directive governs it. The constructors are removed from the frame's window here, before the
 *    first view is compiled, and cannot be put back.
 *  - NAVIGATING ITS OWN FRAME. The embedding page's `frame-src` is checked on every navigation of
 *    the frame (and every redirect hop), so the browser refuses one to any host the page did not
 *    name. Nothing inside the frame can cancel the rest — an opaque-origin document has its
 *    Navigation API events disabled — so nothing here pretends to. The line is drawn outside it:
 *    the frame is served from an origin of its own (app/canvasOrigin.mjs), the only one the page
 *    names, and the server's door (app/api/frameDoor.mjs) refuses any URL there but the frame's own
 *    files, and refuses the terminal to a frame of another origin.
 *
 *  Idempotent; a no-op outside a browser. */
const WEBRTC = ["RTCPeerConnection", "webkitRTCPeerConnection", "mozRTCPeerConnection", "RTCDataChannel", "RTCRtpSender", "RTCRtpReceiver", "RTCIceTransport"] as const;

let locked = false;

export function lockFrame(win: (Window & typeof globalThis) | undefined = typeof window === "undefined" ? undefined : window): { webrtc: boolean } {
  if (!win) return { webrtc: false };
  if (!locked) {
    for (const name of WEBRTC) {
      try {
        Object.defineProperty(win, name, { value: undefined, configurable: false, writable: false });
      } catch { /* a non-configurable global stays; nothing in the frame relies on it */ }
    }
    locked = true;
  }
  return { webrtc: WEBRTC.every((n) => (win as unknown as Record<string, unknown>)[n] === undefined) };
}
