/** `POST /api/desktop/page` — a page (the workspace README by default) rendered for THIS viewer
 *  (PRD 0.13.4 D17, UI 7aa/7cc). Not stored, no model call. The BFF does only what needs the
 *  viewer's own key at crm-api, then agent-api composes the render:
 *    1. the page's source (agent-api, the viewer's read access) → which `{{crm …}}` and card blocks it holds;
 *    2. each `{{crm …}}` block → crm-api `blocks/read` (fields this viewer may read, each marked
 *       whether the workspace may see it too); each card block → the card seam (`cardBlocks.ts`);
 *       the desk's cards (for `{{waiting-on-you}}` and card chips);
 *    3. agent-api `desktop/page` with those answers, keyed by the block's grammar key.
 *  A crm that refuses or is down costs only its blocks (one quiet state each). Body:
 *  `{ workspace?: <slug>, path?: <page.md>, since?: <epoch s> }`. */
import { blocksIn, CARD_BLOCKS } from "../../../../minutes/readme/grammar";
import { cardRowsFor } from "../cardBlocks";
import { GATEWAY_URL, gatewayPost, json, otherOrigin, personKey, relay } from "../desk";
import { deskCards } from "../deskCards";

export const dynamic = "force-dynamic";
const MAX_CRM_BLOCKS = 20;

async function source(key: string, workspace: string, path: string): Promise<string | null> {
  const qs = new URLSearchParams({ path });
  if (workspace && workspace !== "personal") qs.set("slug", workspace);
  try {
    const r = await fetch(`${GATEWAY_URL}/agent/workspace/file?${qs}`, { headers: { "X-API-Key": key }, cache: "no-store", signal: AbortSignal.timeout(15000) });
    if (!r.ok) return null;
    return String(((await r.json()) as { content?: unknown })?.content ?? "");
  } catch { return null; }
}

function crmView(b: { positional: string[]; args: Record<string, string> }): string {
  return b.positional[0] || ["company", "person", "deals", "timeline", "people"].find((k) => k in b.args) || "company";
}

export async function POST(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to see this page" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: Record<string, unknown> = {};
  try { body = ((await req.json()) ?? {}) as Record<string, unknown>; } catch { body = {}; }
  const workspace = typeof body.workspace === "string" ? body.workspace.slice(0, 200) : "personal";
  const path = typeof body.path === "string" ? body.path.slice(0, 300) : "README.md";
  const since = typeof body.since === "number" ? body.since : undefined;
  try {
    const [md, cards] = await Promise.all([source(key, workspace, path), deskCards(key)]);
    const blocks = md ? blocksIn(md) : [];
    const crm: Record<string, unknown> = {};
    const cardBlocks: Record<string, unknown> = {};
    await Promise.all(blocks.slice(0, MAX_CRM_BLOCKS).map(async (b) => {
      if (b.name === "crm") {
        try {
          const r = await gatewayPost("/crm/blocks/read", key, { workspace: workspace === "personal" ? "" : workspace, view: crmView(b), args: b.args });
          crm[b.key] = r.ok ? await r.json() : null;
        } catch { crm[b.key] = null; }
      } else if (CARD_BLOCKS.includes(b.name)) {
        cardBlocks[b.key] = await cardRowsFor(b, key, workspace);
      }
    }));
    return await relay(await gatewayPost("/agent/desktop/page", key, {
      workspace, path, cards, crm, card_blocks: cardBlocks, ...(since !== undefined ? { since } : {}),
    }));
  } catch {
    return json({ detail: "This page is temporarily unavailable" }, 503);
  }
}
