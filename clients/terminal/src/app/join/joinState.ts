/** joinState — everything `/join?i=<token>` decides, as pure functions.
 *
 *  The page is a card with four moving parts (what the invite is · whether the visitor is signed
 *  in · which address it is bound to · what went wrong), and every one of them is a sentence
 *  somebody reads once, at the least forgiving moment there is: they clicked a link a colleague
 *  sent them. So the decisions live here, with no DOM and no fetch, and the component below is the
 *  rendering of them.
 *
 *  ⚠ NEVER A 404 (Vexa-ai/vexa#1635). The founder minted an invite, opened it, and got *"not
 *  found"* — from a host with no such page. A token that is expired, spent, revoked, unknown or
 *  bound to somebody else is not a missing page: it is a thing that HAPPENED, and each of them
 *  gets one sentence saying which. `refusal()` below is the closed set, and its default arm is a
 *  sentence too — an unmapped reason must degrade to "this link cannot be used", never to nothing.
 */

/** What `GET /api/join/preview?i=` answers with (agent-api's invite preview, passed through). */
export interface InvitePreview {
  /** The workspace's directory slug — what `accept` resolves and what `by-slug` looks up. */
  workspace_id: string;
  /** Its canonical id, when the registry knows it. The front page is `/w/<id>`. */
  id?: string | null;
  /** The human name — "OeNB", not `oenb-a1b2c3`. Falls back to the slug server-side. */
  name?: string | null;
  purpose?: string | null;
  role: string;
  mode: string;
  expires_at?: number | null;
  /** The addresses a bound invite admits. Empty for an open link. */
  restricted_to?: string[];
  /** The inviter, by email where we have one, else their opaque subject. */
  shared_by?: string | null;
  valid: boolean;
  /** Why not, when `valid` is false: "expired" · "used_up" · "revoked". */
  reason?: string | null;
}

/** The screen the page is on. `preview` is the only one that offers a way in. */
export type JoinScreen = "loading" | "preview" | "refused" | "joining" | "joined";

/** Every way an invite can fail to let somebody in — a CLOSED set, one sentence each. */
export type RefusalKind =
  | "no-token"
  | "unknown"
  | "expired"
  | "spent"
  | "revoked"
  | "wrong-address"
  | "unreachable";

/** What each role can do, said as the thing the person will actually do with it. */
export function roleSentence(role: string): string {
  switch (role) {
    case "owner":
      return "you can read and write its pages, and invite other people";
    case "contributor":
      return "you can read and write its pages";
    case "viewer":
      return "you can read its pages";
    default:
      // An unknown role is still a real grant; say what we know rather than inventing a capability.
      return "you have been given access to its pages";
  }
}

/** A person, as this card should name them: the local part of an address is what people call each
 *  other, and the full address is the honest fallback when it is not one. Never invents a name. */
export function inviterName(sharedBy?: string | null): string {
  const raw = (sharedBy || "").trim();
  if (!raw) return "Someone";
  const at = raw.indexOf("@");
  if (at <= 0) return raw;                       // an opaque subject id — say it plainly
  const local = raw.slice(0, at);
  const word = local.split(/[._+-]/).filter(Boolean)[0] || local;
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** The one sentence at the top of the card:
 *  *Dmitry invited you to OeNB as a contributor: you can read and write its pages.* */
export function inviteSentence(p: InvitePreview): string {
  const where = (p.name || p.workspace_id || "a workspace").trim();
  return `${inviterName(p.shared_by)} invited you to ${where} as a ${p.role}: ${roleSentence(p.role)}.`;
}

/** The address a BOUND invite admits, or null for an open link. Exactly one address prefills and
 *  locks the sign-in field; several (one invite, several people) locks nothing, because we cannot
 *  know which of them is at the keyboard. */
export function boundAddress(p: InvitePreview | null): string | null {
  if (!p || p.mode !== "restricted") return null;
  const list = (p.restricted_to || []).filter(Boolean);
  return list.length === 1 ? list[0] : null;
}

/** One sentence per way this link cannot be used. Never a status code, never a 404. */
export function refusal(kind: RefusalKind): string {
  switch (kind) {
    case "no-token":
      return "This link is missing its invite — ask whoever sent it for the full link.";
    case "expired":
      return "This invite has expired. Ask whoever sent it for a new one.";
    case "spent":
      return "This invite has already been used. Ask whoever sent it for a new one.";
    case "revoked":
      return "This invite was withdrawn. Ask whoever sent it for a new one.";
    case "wrong-address":
      return "This invite was sent to a different address. Sign in with the address it was sent to, or ask whoever sent it to invite this one.";
    case "unreachable":
      return "This invite could not be checked just now. Try the link again in a moment.";
    case "unknown":
    default:
      return "This invite link is not valid. Ask whoever sent it for a new one.";
  }
}

/** A preview that came back `valid: false` → which refusal. `reason` is agent-api's closed set. */
export function refusalForReason(reason?: string | null): RefusalKind {
  switch (reason) {
    case "expired":
      return "expired";
    case "used_up":
      return "spent";
    case "revoked":
      return "revoked";
    default:
      return "unknown";
  }
}

/** A preview fetch that did not come back 200 → which refusal. 404 is agent-api saying the token
 *  matches nothing (deliberately indistinguishable from a workspace that does not exist); anything
 *  else is our side being unable to answer, which is a different sentence because it is temporary. */
export function refusalForPreviewStatus(status: number): RefusalKind {
  return status === 404 ? "unknown" : "unreachable";
}

/** A redeem that was refused → which refusal. The statuses are `accept`'s own:
 *  403 the verified address is not on the invite · 410 revoked/expired/fully used · 404 no such
 *  token. 410 cannot distinguish spent from expired at the redeem edge, and the honest sentence for
 *  the pair is the one that does not claim to know which. */
export function refusalForAcceptStatus(status: number): RefusalKind {
  if (status === 403) return "wrong-address";
  if (status === 410) return "spent";
  if (status === 404) return "unknown";
  return "unreachable";
}

/** Where a redeemed invite lands: the workspace's own front page when the id resolved, else the
 *  terminal. Landing on `/` is a degradation, not a failure — they ARE in the workspace by then. */
export function landingPath(workspaceId?: string | null): string {
  const id = (workspaceId || "").trim();
  return id ? `/w/${encodeURIComponent(id)}` : "/";
}

/** The `next=` a sign-in carries so the round trip comes back to THIS invite and redeems it. */
export function returnPath(token: string): string {
  return `/join?i=${encodeURIComponent(token)}`;
}
