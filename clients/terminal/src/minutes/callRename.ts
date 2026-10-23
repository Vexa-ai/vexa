/** RENAME A CALL — the write half of a call's title.
 *
 *  Founder, 2026-10-11, on the Calls view and the meeting header: *"call rename needed, probably
 *  same as for chats"*.
 *
 *  THE TITLE IS THE MEETING'S OWN. It is `data.title` on the meeting row in meeting-api, the same
 *  field a planned meeting's title, the MCP `annotate_meeting` tool and the REST annotate route all
 *  write. This module writes it through that one front door — `POST /api/meetings/{row id}/annotate`
 *  → gateway `/meetings/{id}/annotate` → meeting-api — and keeps no copy anywhere in the terminal:
 *  the store is patched with what the route just stored, then re-read from the list.
 *
 *  WHO MAY. The annotate route matches the row's `user_id`, so only the meeting's owner can rename
 *  it; anyone else gets a 404, whatever the terminal shows. The terminal mirrors that rule so a
 *  person the call is shared with is never offered a control that would only fail: `canRenameCall`
 *  is false for a `shared` row (meeting-api's list marks every row the caller does not own).
 *
 *  An empty title is the route's "clear": the call goes back to its default label. */
import type { MeetingMock } from "../surfaces/meetingModel";
import { applyMeetingTitle, refreshMeetings } from "../surfaces/liveMeetings";

/** meeting-api caps a title at 512 characters; the input stops there too. */
export const CALL_TITLE_MAX = 512;

/** Only the owner renames. A row we do not hold yet is not renameable either — there is nothing to
 *  say whose it is. */
export function canRenameCall(m: Pick<MeetingMock, "shared"> | null | undefined): boolean {
  return !!m && !m.shared;
}

export class CallRenameError extends Error {
  constructor(readonly status: number) {
    super(status === 404 ? "This call is not yours to rename." : "Couldn't save. Try again.");
  }
}

/** Write a call's title. Throws `CallRenameError` on any refusal and changes nothing locally. */
export async function renameCall(meetingId: string, title: string, fetcher: typeof fetch = fetch): Promise<void> {
  const t = String(title ?? "").trim().slice(0, CALL_TITLE_MAX);
  let r: Response;
  try {
    r = await fetcher(`/api/meetings/${encodeURIComponent(meetingId)}/annotate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }),
    });
  } catch {
    throw new CallRenameError(0);
  }
  if (!r.ok) throw new CallRenameError(r.status);
  applyMeetingTitle(meetingId, t);
  refreshMeetings();
}
