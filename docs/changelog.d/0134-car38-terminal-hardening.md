- **The terminal runs no text as code, and every response carries a policy.** Workspace pages are drawn
  from a parsed tree instead of compiled in the browser, so the pages' Content-Security-Policy drops
  `'unsafe-eval'`; `<style>` elements need the response's nonce. API answers, the `/b/` proxy and static
  files now carry a policy too. See [Security](/security-compliance#the-terminal-in-the-browser).
- **An emailed sign-in link works only in the browser that asked for it.** Opened elsewhere (a phone, another
  profile) it says so and offers to send a new link there. Links sent before the upgrade stop working;
  ask for a new one.
- **Older browser sign-ins end.** A browser still holding a sign-in key from before sessions, or a
  session older than 30 days, is asked to sign in again; admin-api bounds such credentials on boot
  (MIGRATION-0011).
- **Browsers send the websocket key in the subprotocol list.** `new WebSocket(url, ["vexa.v1",
  "vexa.key.<key>"])`. A key in the URL (`/ws?api_key=`) still works for clients built against
  0.12, deprecated: 0.13.6 turns it off (see [What changed in 0.13.4: auth](/changes-0-13-4-auth)). See
  [Stream a transcript](/how-to/stream-transcript).
- **Canvas views cannot open WebRTC**, and the local-dev Minutes routes are no longer built into
  production images.
