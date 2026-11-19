/** `GET /api/desktop/workspace-settings` and `POST /api/desktop/workspace-settings` — the person's own
 *  settings about each workspace (#1783, R3 3.4; founder-approved rule: per-person state lives on the
 *  CRM `member_of` relation, crm-api the sole writer). GET reads `/crm/desk/workspaces/read`; POST
 *  `{ workspace, settings }` writes `/crm/desk/workspaces/set`. Both with the person's own key; stored
 *  nowhere here, never in the browser. A refusal is answered as given. */
import { gatewayPost, json, otherOrigin, personKey, relay } from "../desk";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to read your settings" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  try {
    return await relay(await gatewayPost("/crm/desk/workspaces/read", key, {}));
  } catch {
    return json({ detail: "Your settings are temporarily unavailable" }, 503);
  }
}

export async function POST(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to change your settings" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  let body: { workspace?: unknown; settings?: unknown };
  try { body = (await req.json()) ?? {}; } catch { return json({ detail: "Invalid JSON" }, 400); }
  if (typeof body.workspace !== "string" || !body.workspace || body.workspace.length > 128) return json({ detail: "workspace must be a workspace slug" }, 400);
  if (!body.settings || typeof body.settings !== "object" || Array.isArray(body.settings)) return json({ detail: "settings must be an object" }, 400);
  try {
    return await relay(await gatewayPost("/crm/desk/workspaces/set", key, { workspace: body.workspace, settings: body.settings }));
  } catch {
    return json({ detail: "Your settings are temporarily unavailable" }, 503);
  }
}
