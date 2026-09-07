/** proposalsApi — the desk's short list, read and closed (Vexa-ai/vexa#1614).
 *
 *  Founder, 2026-09-06, on the empty chat: *"that is a short list that is updated by other agents
 *  when they see something as JTBD, can have up to 10 items"*. The agents write it (the post-meeting
 *  turn files what the report committed you to); this reads it.
 *
 *  A PLAIN READ, AND THAT IS THE DESIGN. `GET /api/proposals` is a file read behind the gateway's
 *  identity injection — no turn, no model, no composition (the #1584 rule the founder restated on
 *  #1614: the row is rendered from state). A surface that asked a model what to offer would cost a
 *  turn every time somebody opened an empty chat and would answer differently each time.
 *
 *  IT NEVER THROWS. This is the one deliberate exception to the fail-loud rule the rest of this
 *  folder holds (P18, `apiClient`): a chat that refused to open because a suggestion list could not
 *  be fetched would be a strictly worse product than a chat with no suggestions. The standing acts
 *  are the client's own and are there either way, so an empty answer degrades to exactly the row
 *  this surface had before the store existed.
 */

/** One row of the store. The schema IS the contract — `source` and `act` together are its identity,
 *  `since` is when it was FIRST seen (never when a flow last re-ran), `status` gates the offer. */
export interface DeskProposal {
  id: string;
  /** where the job was seen — `meeting:97`, `page:kg/entities/company/oenb.md` */
  source: string;
  /** the source in human words, for the chip — the meeting's title, the page's name */
  source_label?: string;
  /** the one line: what the chip says, and what is said into the chat on a click */
  act: string;
  /** ISO-8601 UTC, first sighting */
  since?: string;
  status?: string;
  /** which agent saw it — one writer per item */
  by?: string;
}

/** The OPEN rows, newest first, at most ten. `[]` for anything that went wrong, deliberately. */
export async function listProposals(): Promise<DeskProposal[]> {
  try {
    const r = await fetch("/api/proposals", { cache: "no-store" });
    if (!r.ok) return [];
    const data = (await r.json()) as { items?: DeskProposal[] };
    return (data.items ?? []).filter((i) => i && i.id && i.act);
  } catch {
    return [];
  }
}

/** The row leaves. `ran` is a click that fired its act; `dismissed` is the person saying no — and
 *  the two are kept apart because only one of them is feedback about the proposal.
 *
 *  Fire-and-forget: the client has already removed the row from the list it is rendering, so a
 *  failed close costs a chip that comes back on the next load, never a click that did nothing. */
export async function resolveProposal(id: string, status: "ran" | "dismissed"): Promise<void> {
  try {
    await fetch("/api/proposals/resolve", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id, status }),
    });
  } catch {
    /* see the docstring */
  }
}
