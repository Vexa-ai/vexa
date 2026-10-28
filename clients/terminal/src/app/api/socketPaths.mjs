// The browser sockets the terminal server relays to the gateway (server.mjs), and nothing else.
//
// `/ws` is the meetings multiplex. `/voice-notes/{id}/stream` is a voice note's audio socket (#1783):
// the browser streams its microphone through it, keyless; the server adds the person's key from the
// session cookie, exactly as for `/ws`. A voice note's id is one opaque segment — letters, digits,
// `-` and `_` only — so nothing in it can reshape the upstream path.

/** The gateway path a browser socket at `pathname` is relayed to, or null when it is not relayed. */
export function upstreamSocketPath(pathname) {
  if (pathname === "/ws") return "/ws";
  const m = /^\/voice-notes\/([A-Za-z0-9_-]{1,64})\/stream$/.exec(pathname || "");
  return m ? `/voice-notes/${m[1]}/stream` : null;
}
