// Who may open a relayed browser socket (server.mjs), and with which key.
//
// The relayed sockets carry the person's session: the server adds their key from the session cookie.
// So an upgrade is taken only from this terminal's own pages:
//   • an `Origin` header, when present, must be this host (same-origin) or the configured public
//     origin (NEXTAUTH_URL, else TERMINAL_URL) — a browser always sends one on a WebSocket;
//   • a voice note's audio socket needs the session cookie: it never falls back to a deployment key.
//     `/ws` keeps the REST proxy's fallback (src/app/api/proxyAuth.ts) for single-key deployments.

// The one cookie reader the REST side agrees with (last value of a repeated name wins).
import { readCookie } from "./auth/cookieHeader.mjs";
export { readCookie };

function originOf(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.origin : null;
  } catch {
    return null;
  }
}

/** The public origins this terminal is configured to be served from. */
export function configuredOrigins(env) {
  return [env.NEXTAUTH_URL, env.TERMINAL_URL]
    .map((v) => (v || "").trim())
    .filter(Boolean)
    .map(originOf)
    .filter(Boolean);
}

/** True when an upgrade's `Origin` is this terminal's own (or absent: not a browser). */
export function originAllowed(headers, env) {
  const origin = headers.origin;
  if (origin === undefined) return true;
  const parsed = originOf(origin);
  if (!parsed) return false;
  if (configuredOrigins(env).includes(parsed)) return true;
  const host = (headers.host || "").toLowerCase();
  return host !== "" && new URL(parsed).host.toLowerCase() === host;
}

function isVoiceNoteSocket(upstreamPath) {
  return upstreamPath.startsWith("/voice-notes/");
}

/** Why an upgrade to `upstreamPath` is refused (an HTTP status and a reason), or null to take it. */
export function upgradeRefusal(req, upstreamPath, env, cookieName) {
  if (!originAllowed(req.headers || {}, env)) return { status: 403, reason: "Forbidden" };
  if (isVoiceNoteSocket(upstreamPath) && !readCookie(req.headers?.cookie, cookieName)) {
    return { status: 401, reason: "Unauthorized" };
  }
  return null;
}

/** The key sent upstream for an upgrade: the session cookie's token; `/ws` alone falls back to the
 *  deployment keys, as the REST proxy does (cookie → VEXA_API_KEY → VEXA_BOT_API_KEY → ""). */
export function upstreamKey(req, upstreamPath, env, cookieName) {
  const token = readCookie(req.headers?.cookie, cookieName);
  if (token) return token;
  if (isVoiceNoteSocket(upstreamPath)) return "";
  return env.VEXA_API_KEY || env.VEXA_BOT_API_KEY || "";
}

/** Answer a refused upgrade on the raw socket and close it. */
export function refuseUpgrade(socket, { status, reason }) {
  try {
    socket.write(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
  } catch {
    // the peer may already be gone
  }
  socket.destroy();
}
