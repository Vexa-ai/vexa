/** `GET /api/desktop` — the desktop as drawn (#1783, desktop ADR §2.1, D1). A read model composed
 *  PER REQUEST and stored nowhere: crm-api's `desk/read` (the person's arrangement), then agent-api's
 *  `desktop/sections` with the placed refs (the tiles, under the viewer's access), then the pure
 *  `composeDesktop`. Both calls carry the signed-in person's own key through the one edge.
 *
 *  A refusal from crm (the `desktop` or `crm` feature off: 404, or 403 `feature_disabled`) is
 *  answered as given, so the page falls back to the current home. */
import { composeDesktop, placedRefs, type Desk, type DesktopSections } from "../../../platform/desktop";
import { gatewayPost, json, otherOrigin, personKey, relay } from "./desk";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const key = await personKey();
  if (!key) return json({ detail: "Sign in to see your desktop" }, 401);
  if (otherOrigin(req)) return json({ detail: "Invalid request origin" }, 403);
  try {
    const read = await gatewayPost("/crm/desk/read", key, {});
    if (!read.ok) return relay(read);
    const desk = (await read.json()) as Desk;
    const tiles = await gatewayPost("/agent/desktop/sections", key, { refs: placedRefs(desk) });
    if (!tiles.ok) return relay(tiles);
    return json(composeDesktop(desk, (await tiles.json()) as DesktopSections));
  } catch {
    return json({ detail: "The desktop is temporarily unavailable" }, 503);
  }
}
