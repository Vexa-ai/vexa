/** The 7g ask's BFF half (0.13.4 S5): an in-chat ```ask card is a PENDING ITEM in agent-api, and a
 *  press answers that item with the nonce and content hash it was opened with. Both calls travel
 *  with the signed-in person's OWN key (the session cookie) — never a deployment key — because the
 *  press is the person's attestation. Not a route file: route files export only handlers. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../desktop/desk";

export async function forwardAsk(req: Request, path: string): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to answer" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown;
  try { body = await req.json(); } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ detail: "Expected an object" }, 400);
  try {
    return await relay(await gatewayPost(path, key, body));
  } catch {
    return json({ detail: "Answers are temporarily unavailable" }, 503);
  }
}
