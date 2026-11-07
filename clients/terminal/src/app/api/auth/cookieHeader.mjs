// One cookie out of a raw `Cookie` header, for the custom server's socket upgrades (server.mjs).
//
// The REST side reads the same cookie through Next's `cookies()`. Both must name the same value, or a
// browser holding two cookies of one name gets two different identities: its pages work while its
// sockets are refused.

/** Pull a single cookie value out of a raw `Cookie` header; undefined if absent. When the name repeats
 *  (a parent-domain cookie beside this host's), the LAST one wins — exactly as Next's `cookies()`
 *  decides it, so the socket and the REST proxy present the same key. */
export function readCookie(header, name) {
  if (!header) return undefined;
  let found;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    try { found = decodeURIComponent(part.slice(eq + 1).trim()); } catch { /* undecodable: skipped, as Next does */ }
  }
  return found;
}
