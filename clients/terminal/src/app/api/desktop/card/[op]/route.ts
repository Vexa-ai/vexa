/** `POST /api/desktop/card/<op>` — the person's own card verbs (#1783, desktop ADR §2.3, D2), behind
 *  crm-api's `desktop_cards` feature. `read|create|update|members|membership|list` go to crm-api's
 *  `/crm/card/<op>`; `review` (Add / Not now on a card proposal) goes to `/crm/review`. Each travels
 *  through the one edge with the signed-in person's own key. crm-api enforces every rule (who may
 *  move a stage, who may add a member, revisions); this route stores nothing and answers what crm
 *  answered. The agent's verbs (`card_propose`, `card_update_propose`) are MCP tools, never here. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../../desk";

export const dynamic = "force-dynamic";

const UPSTREAM: Record<string, string> = {
  read: "/crm/card/read",
  create: "/crm/card/create",
  update: "/crm/card/update",
  members: "/crm/card/members",
  membership: "/crm/card/membership",
  list: "/crm/card/list",
  review: "/crm/review",
};

export async function POST(req: Request, ctx: { params: Promise<{ op: string }> }): Promise<Response> {
  const { op } = await ctx.params;
  const path = Object.prototype.hasOwnProperty.call(UPSTREAM, op) ? UPSTREAM[op] : null;
  if (!path) return json({ detail: "Unknown card operation" }, 400);
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to use cards" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown;
  try { body = await req.json(); } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ detail: "Expected an object" }, 400);
  try {
    return await relay(await gatewayPost(path, key, body));
  } catch {
    return json({ detail: "Cards are temporarily unavailable" }, 503);
  }
}
