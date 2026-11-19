"use client";
/** ROOMS OF TWO IN THE SIDEBAR (S4 `s4-rooms`, UI 7hh; feature `shell2`). A room of two is a room
 *  whose visibility is `direct` (its workspace is a hidden `pair-…` workspace). In the sidebar it
 *  is named by the OTHER person and shows their face, presence dot and unread count; under the
 *  Rooms filter the rooms of two sit together under "People".
 *
 *  The other person and the unread count come on the chat list's own row (agent-api
 *  `GET /api/sessions`: `visibility`, `other_subject`, `other_name`, `unread`). Presence comes from the
 *  desktop's member lists (`usePresence`). */
import { Identity } from "../../ui-kit";
import { personPhotoUrl } from "../../platform/identity";
import { roomOf } from "../../surfaces/roomState";
import type { Presence } from "./people";

type Peer = { principal: string; name: string };

/** Is this chat a room of two? The chat list says so (`visibility: "direct"`); a remembered room
 *  read says so too. */
export function isPairRoom(session: string | null | undefined, visibility?: string | null): boolean {
  if (visibility === "direct") return true;
  const r = session ? roomOf(session) : null;
  return !!r && (r.visibility === "direct" || (r.workspace ?? "").startsWith("pair-"));
}

/** The other person in a room of two: named on the chat list's own row (`other_subject`,
 *  `other_name`), so nothing is read per room. */
export function pairPeerOf(row: { pairPeer?: Peer } | null | undefined): Peer | null {
  return row?.pairPeer ?? null;
}

/** The row's leading face: the other person's photo with a presence dot. */
export function PairFace({ peer, presence }: { peer: Peer; presence: Presence }) {
  return <span className="vx-s2-face" data-presence={presence ?? undefined}>
    <Identity kind="person" id={peer.principal} name={peer.name} size={16} user src={personPhotoUrl(peer.principal)} /></span>;
}

/** Test seam (nothing is cached any more). */
export function resetPairPeers(): void { /* the chat list carries the peer */ }
