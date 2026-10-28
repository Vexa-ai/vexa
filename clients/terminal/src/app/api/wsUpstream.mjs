// Which WebSocket upgrades server.mjs proxies to the gateway, and to which gateway path.
//
// Three shapes are proxied: `/ws` (live meeting frames), a voice note's audio socket
// (`/voice-notes/{id}/stream`, ./socketPaths.mjs) and a browser session's live view,
// `/bot-accounts/sessions/<32 hex>/vnc` (the noVNC client in the sign-in view). Who may open each and
// with which key is ./socketUpgrade.mjs. Any other path under `/bot-accounts/` is refused; everything else (Next's HMR in dev)
// is left to Next.

import { upstreamSocketPath } from "./socketPaths.mjs";

const VNC = /^\/bot-accounts\/sessions\/[a-f0-9]{32}\/vnc$/;

/** `{ path }` to proxy to the gateway at that path, `{ refuse: true }` to drop the socket, or
 *  `null` to leave the upgrade to Next. */
export function wsUpstream(pathname) {
  const relayed = upstreamSocketPath(pathname);
  if (relayed) return { path: relayed };
  if (VNC.test(pathname)) return { path: pathname };
  if (pathname === "/bot-accounts" || pathname.startsWith("/bot-accounts/")) return { refuse: true };
  return null;
}
