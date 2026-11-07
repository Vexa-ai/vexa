/** `POST /api/desktop/<arrange|proposal|undo>` — the person's own arranging verbs (#1783, desktop
 *  ADR §2.3a, D1), forwarded to crm-api's `/crm/desk/<op>` through the one edge with the signed-in
 *  person's own key. crm-api enforces the rule (direct only for the person; `base_version` conflicts
 *  answer 409 `desk_changed`); this route stores nothing and answers what crm answered. A static
 *  segment beats the `[...path]` catch-all, so these never reach agent-api. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../desk";

export const dynamic = "force-dynamic";

const OPS = new Set(["arrange", "proposal", "undo"]);

export async function POST(req: Request, ctx: { params: Promise<{ op: string }> }): Promise<Response> {
  const { op } = await ctx.params;
  if (!OPS.has(op)) return json({ detail: "Unknown desktop operation" }, 400);
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to arrange your desktop" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown;
  try { body = await req.json(); } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ detail: "Expected an object" }, 400);
  try {
    return await relay(await gatewayPost(`/crm/desk/${op}`, key, body));
  } catch {
    return json({ detail: "The desktop is temporarily unavailable" }, 503);
  }
}
