/** `POST /api/asks/<pending_id>/answer {nonce, content_hash, reply, text?}` → agent-api
 *  `/api/asks/<pending_id>/answer` (gateway `/agent/asks/…`). agent-api checks the nonce and hash
 *  (409 on a replay) and answers `{status: "answered", message}`; the browser then sends `message` as
 *  the person's own chat message. The person's own key; nothing stored. */
import { json } from "../../../desktop/desk";
import { forwardAsk } from "../../ask";

export const dynamic = "force-dynamic";

const ID = /^[A-Za-z0-9._:-]{1,120}$/;

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  if (!ID.test(id)) return json({ detail: "Unknown pending item" }, 400);
  return forwardAsk(req, `/agent/asks/${encodeURIComponent(id)}/answer`);
}
