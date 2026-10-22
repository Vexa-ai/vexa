/** What the Meeting Canvas frame takes away before any view code runs (S03T-1).
 *
 *  The frame holds data the reader was shown, and a view is workspace-written code. Its policy
 *  (`canvasFrameContentSecurityPolicy`) closes every fetch, and the sandbox closes popups, forms and
 *  top-level navigation. Two ways out are left to close or bound:
 *
 *  - WEBRTC. ICE candidate gathering reaches a STUN/TURN host of the view's choosing, and no CSP
 *    directive governs it. The constructors are removed from the frame's window here, before the
 *    first view is compiled, and cannot be put back.
 *  - NAVIGATING ITS OWN FRAME. The embedding page's `frame-src 'self'` is checked on every
 *    navigation of the frame, so the browser refuses one to any other host (and to every redirect
 *    hop). A navigation to a URL on the terminal's own origin is not refused: it reaches only this
 *    server, without the reader's cookies (the frame's origin is opaque, so `SameSite` cookies stay
 *    home). The Navigation API cannot cancel it — an opaque-origin document has its navigation
 *    events disabled — so nothing here pretends to.
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
