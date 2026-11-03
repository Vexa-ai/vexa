/** `POST /api/desktop/attention` — what needs the person and what is working where (#1783, R3
 *  `home2`, decision E1). A read model composed PER REQUEST and stored nowhere: the cards on the
 *  person's desk (crm-api `desk/read`), then agent-api's rule-based `desktop/attention` with them.
 *  No model call anywhere. Body: `{ workspace?: <slug> }` — one workspace's richer sections (the
 *  workspace page), or every workspace (the home). A static segment beats `[op]` and the catch-all. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../desk";
import { deskCards } from "../deskCards";

export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to see what needs you" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: unknown = {};
  try { body = await req.json(); } catch { body = {}; }
  const ws = (body as { workspace?: unknown } | null)?.workspace;
  if (ws !== undefined && (typeof ws !== "string" || ws.length > 200)) return json({ detail: "workspace must be a workspace slug" }, 400);
  try {
    const cards = await deskCards(key);
    return await relay(await gatewayPost("/agent/desktop/attention", key, { ...(ws ? { workspace: ws } : {}), cards }));
  } catch {
    return json({ detail: "What needs you is temporarily unavailable" }, 503);
  }
}
