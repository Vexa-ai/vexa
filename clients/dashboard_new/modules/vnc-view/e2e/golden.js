/**
 * golden.js — the single source of truth for the props the L4 harness mounts and asserts on.
 *
 * Shared by BOTH sides of the gate so they can never drift:
 *   • the fixtures (vnc-entry.tsx) import these and mount <VncView> with them
 *   • the spec (vnc-render.spec.ts) imports these and asserts the rendered DOM matches
 *
 * GOLDEN_VNC_URL is a real per-bot noVNC URL in the gateway's `/b/{id}/vnc/...` shape (the form the
 * vendored dashboard composes). The empty case has no constant — the empty fixture mounts vncUrl="".
 */

/** A golden per-bot noVNC URL, shaped exactly as the gateway will route `/b/{id}/vnc/*`. */
export const GOLDEN_VNC_URL =
  "/b/bot-token-abc123/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=false&path=b/bot-token-abc123/vnc/websockify";

/** The placeholder text the empty-state fixture mounts (must match the spec's assertion). */
export const GOLDEN_PLACEHOLDER_TEXT = "Connecting to bot…";
