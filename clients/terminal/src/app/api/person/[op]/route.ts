/** `POST /api/person/<op>` — the signed-in person's own Person (PRD 0.13.4 D35: one Person, many
 *  addresses). `me` reads how many accounts answer for them, their addresses (their own in full, any
 *  other masked), the primary address and the merge card waiting for them; `merge` confirms or
 *  declines that card, changes the primary address, or offers another address of theirs; `profile` reads
 *  and edits their own profile (the claimed record's person-owned fields), claims, unclaims, and
 *  switches the public page. All go to
 *  crm-api's `/crm/person/<op>` through the one edge with the person's OWN key (never a deployment
 *  key: nobody confirms who someone is on their behalf). crm-api enforces every rule; this route
 *  stores nothing and answers what crm answered. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../../desktop/desk";

export const dynamic = "force-dynamic";

const UPSTREAM: Record<string, string> = {
  me: "/crm/person/me",
  merge: "/crm/person/merge",
  profile: "/crm/person/profile",
};

export async function POST(req: Request, ctx: { params: Promise<{ op: string }> }): Promise<Response> {
  const { op } = await ctx.params;
  const path = Object.prototype.hasOwnProperty.call(UPSTREAM, op) ? UPSTREAM[op] : null;
  if (!path) return json({ detail: "Unknown person operation" }, 400);
  const key = await personKey();
  if (!key) return json({ detail: "Sign in first" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown;
  try { body = await req.json(); } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ detail: "Expected an object" }, 400);
  try {
    return await relay(await gatewayPost(path, key, body));
  } catch {
    return json({ detail: "Your accounts cannot be read just now" }, 503);
  }
}
