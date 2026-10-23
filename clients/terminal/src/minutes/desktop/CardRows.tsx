"use client";
/** WHAT WAITS FOR THE PERSON'S WORD (#1783, desktop ADR §2.3/§2.10, D2; UI decisions 15c).
 *
 *  Quiet rows, never cards on the desktop by themselves:
 *   - a card PROPOSAL says who proposed it ("Vexa suggests", or a person's name) and why; Add accepts
 *     it (crm review accept: the card is made, the person is on it), Not now rejects it;
 *   - an INVITE says who added the person and to what; Accept puts the card on their desktop, Decline
 *     leaves it. Being added never places a card by itself. */
import type { CardProposal, CardView } from "../../platform/desktop";
import { answerInvite, reviewProposal, usePeople } from "./cardsApi";

export function ProposalRow({ p }: { p: CardProposal }) {
  const { nameOf } = usePeople();
  const lead = p.author_kind === "agent" || !p.author ? "Vexa suggests" : `${nameOf(p.author)} suggests`;
  const what = p.kind === "change" && p.card ? `a change to ${p.card.title}` : p.title;
  return (
    <div className="vx-dk-pending" data-card-proposal={p.id} data-by={p.author_kind}>
      <span className="vx-dk-plead">{lead}:</span>
      <span className="vx-dk-ptitle">{what}</span>
      {p.reason && <span className="vx-dk-preason">· {p.reason}</span>}
      <span className="vx-dk-pacts">
        <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => { void reviewProposal(p.id, true); }}>Add</button>
        <button type="button" className="vx-kh-btn" onClick={() => { void reviewProposal(p.id, false); }}>Not now</button>
      </span>
    </div>
  );
}

/** Who added the person: the card's member who is not them (the server says who is invited). */
function inviter(c: CardView & { invited_by?: string | null }): string | null {
  if (typeof c.invited_by === "string" && c.invited_by) return c.invited_by;
  return c.members.find((m) => m.status === "member")?.subject ?? null;
}

export function InviteRow({ c }: { c: CardView }) {
  const { nameOf } = usePeople();
  const by = inviter(c);
  return (
    <div className="vx-dk-pending" data-card-invite={c.id}>
      <span className="vx-dk-plead">{by ? `${nameOf(by)} added you to` : "You were added to"}</span>
      <span className="vx-dk-ptitle">{c.title}</span>
      <span className="vx-dk-pacts">
        <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => { void answerInvite(c.id, true); }}>Accept</button>
        <button type="button" className="vx-kh-btn" onClick={() => { void answerInvite(c.id, false); }}>Decline</button>
      </span>
    </div>
  );
}
