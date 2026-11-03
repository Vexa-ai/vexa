/** `POST /api/asks/open {session, ask_id}` → agent-api `/api/asks/open` (through the gateway as
 *  `/agent/asks/open`): opens — or finds — the pending item behind a 7g ```ask card and answers
 *  `{pending_id, nonce, content_hash, spec, status, answer?}`. The person's own key; nothing stored. */
import { forwardAsk } from "../ask";

export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  return forwardAsk(req, "/agent/asks/open");
}
