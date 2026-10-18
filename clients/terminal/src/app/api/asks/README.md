# terminal/src/app/api/asks — the 7g ask as a pending item

## How it works

An agent asks in chat with a ```` ```ask ```` fence (`ui-kit/InlineCard.tsx`). The card is backed by a
pending item in agent-api:

- `POST /api/asks/open {session, ask_id}` → gateway `/agent/asks/open` → `{pending_id, nonce, content_hash, spec, status, answer?}`
- `POST /api/asks/<pending_id>/answer {nonce, content_hash, reply: {label, reply?}, text?}` → gateway
  `/agent/asks/<pending_id>/answer` → `{status: "answered", message}`; 409 on a replayed nonce.

The browser sends the returned `message` as the person's own chat message.

## Why it complies

- **The person's own key.** Both routes use `desktop/desk.ts` `personKey()` (the session cookie), never
  the deployment key the catch-all falls back to: the press is the person's attestation (7g).
- **Bound, not typed.** The answer carries the nonce and content hash the card was opened with, so a
  replay or a changed card is refused by agent-api (D13). A failed open or answer never falls back to
  the old unbound `[answer <id>]` text.
- **Stores nothing.** Status and body are relayed as agent-api answered them; the origin check refuses
  cross-site posts.
