/** `POST /api/desktop/refs` — a page's typed references, answered for THIS viewer (#1783, R3
 *  `home2`). Body `{ refs: ["room:<id>", "call:<id>", …] (≤100), workspace?: <slug> }`. agent-api
 *  decides each one under the viewer's access: a reference they may not see comes back as
 *  `{ kind, visible: false }` and nothing else, so a page never names what its reader cannot open.
 *  Card references need the cards on the person's desk, which only crm-api has; it is read only when
 *  a card is asked for. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../desk";
import { deskCards } from "../deskCards";

export const dynamic = "force-dynamic";

const MAX_REFS = 100;

export async function POST(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to read this page" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: { refs?: unknown; workspace?: unknown } = {};
  try { body = (await req.json()) ?? {}; } catch { return json({ detail: "Invalid JSON" }, 400); }
  const refs = (Array.isArray(body.refs) ? body.refs : []).filter((r): r is string => typeof r === "string" && r.length <= 600).slice(0, MAX_REFS);
  const workspace = typeof body.workspace === "string" && body.workspace.length <= 200 ? body.workspace : undefined;
  try {
    const cards = refs.some((r) => r.startsWith("card:")) ? await deskCards(key) : [];
    return await relay(await gatewayPost("/agent/desktop/refs", key, { refs, ...(workspace ? { workspace } : {}), cards }));
  } catch {
    return json({ detail: "This page's references are temporarily unavailable" }, 503);
  }
}
