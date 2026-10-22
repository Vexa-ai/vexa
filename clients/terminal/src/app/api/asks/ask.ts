/** The 7g ask's BFF half (0.13.4 S5): an in-chat ```ask card is a PENDING ITEM in agent-api, and a
 *  press answers that item with the nonce and content hash it was opened with. Both calls travel
 *  with the signed-in person's OWN credential (the session cookie) — never a deployment key — because
 *  the press is the person's attestation. Not a route file: route files export only handlers.
 *
 *  A browser still holding a pre-session login KEY is refused by agent-api with `session_required`.
 *  The refusal goes back marked `sign_in_again`, and the card asks the person to sign in again —
 *  which mints their session — instead of saying "Couldn't send". */
import { gatewayPost, json, otherOrigin, personKey } from "../desktop/desk";
import { isSessionRequired } from "../auth/sessionRequired";

const NO_STORE = { "Cache-Control": "no-store", "Content-Type": "application/json" } as const;

const signInAgain = () => json({ detail: { refused: "session_required", sign_in_again: true, note: "Sign in again to answer" } }, 403);

export async function forwardAsk(req: Request, path: string): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to answer" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown;
  try { body = await req.json(); } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ detail: "Expected an object" }, 400);
  try {
    const first = await gatewayPost(path, key, body);
    const text = await first.text();
    if (!isSessionRequired(first.status, text)) return new Response(text, { status: first.status, headers: NO_STORE });
    return signInAgain();
  } catch {
    return json({ detail: "Answers are temporarily unavailable" }, 503);
  }
}
