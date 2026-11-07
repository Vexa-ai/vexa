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
  /** The human name — "Example Bank", not `bank-a1b2c3`. Falls back to the slug server-side. */
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
  /** The room the invite lands in (PRD 0.13.4 S2), or null for the workspace itself. */
  room?: { id: string; title?: string } | null;
  /** The link itself signs its one address in, now (a mailed invite, inside its window). */
  signin?: boolean;
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
 *  *Dmitry invited you to Example Bank as a contributor: you can read and write its pages.* */
export function inviteSentence(p: InvitePreview): string {
  const ws = (p.name || p.workspace_id || "a workspace").trim();
  const room = (p.room?.title || "").trim();
  const where = room ? `${room} in ${ws}` : ws;
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

/** Where a redeemed invite lands: the workspace's own front page, `/w/<ref>` — the SAME route a
 *  link in a mail or a chat takes (Vexa-ai/vexa#1643), so the invitee's first page and every later
 *  one are opened by one piece of code rather than two that drift.
 *
 *  The id is preferred because it survives a rename; the SLUG is the fallback, and it is a real one
 *  now that the route accepts both — `accept` always returns the slug, while the id needs a second
 *  lookup that can fail without the join failing. Landing on `/` is the last resort and a
 *  degradation, not a failure: they ARE in the workspace by then. */
export function landingPath(workspaceId?: string | null, slug?: string | null): string {
  const ref = (workspaceId || "").trim() || (slug || "").trim();
  return ref ? `/w/${encodeURIComponent(ref)}` : "/";
}

/** The `next=` a sign-in carries so the round trip comes back to THIS invite and redeems it. */
export function returnPath(token: string): string {
  return `/join?i=${encodeURIComponent(token)}`;
}

/** `/join/<token>` → `/join?i=<token>`: the path spelling of a link, onto the page that redeems it. */
export function joinPathFor(token: string): string {
  let t = String(token || "");
  try {
    t = decodeURIComponent(t);
  } catch {
    /* a malformed escape is left as it came; the page refuses it as unknown */
  }
  t = t.trim();
  return t ? `/join?i=${encodeURIComponent(t)}` : "/join";
}

/** The join page for `token`, with an optional note (`n=`) saying why the one-click came back:
 *  `signin` — the link no longer signs in (spent, out of its window, or never a sign-in link), so
 *  the page offers the emailed sign-in link; `unavailable` — it could not be checked just now. */
export function joinPage(token: string, note?: string): string {
  const q = new URLSearchParams();
  if (token) q.set("i", token);
  if (note) q.set("n", note);
  const s = q.toString();
  return s ? `/join?${s}` : "/join";
}

/** Where a joined person lands (PRD 0.13.4 S2): the room the invite named, else the workspace. */
export function roomOrWorkspacePath(workspaceId?: string | null, room?: { id?: string } | null, slug?: string | null): string {
  if (room?.id) return `/r/chat/${encodeURIComponent(room.id)}`;
  return landingPath(workspaceId, slug);
}

/** The one sentence under a refusal that came back from the one-click (`n=`), or "". */
export function oneClickNote(note?: string | null): string {
  switch (note) {
    case "signin":
      return "This link can no longer sign you in by itself. Sign in below and the invite is still yours to accept.";
    case "unavailable":
      return "We could not finish just now. Try again in a moment, or sign in below.";
    case "crosssite":
      return "Press Join on this page to use the invite.";
    case "rate":
      return "Too many attempts. Wait a few minutes, then try again.";
    case "notallowed":
      return "This address is not allowed to sign in to this Vexa. Ask whoever invited you, or its administrator.";
    default:
      return "";
  }
}

/** The address a person recognises as theirs, not enough for anyone else to learn it:
 *  `maria@oenb.at` → `m…@oenb.at`. */
export function maskAddress(email?: string | null): string {
  const e = (email || "").trim();
  const at = e.lastIndexOf("@");
  if (at <= 0) return "";
  return `${e[0]}…${e.slice(at)}`;
}

/** "Not you?" (PRD 0.13.4 D35, S3). One human may sign in with several addresses, and the browser
 *  that opened the link may hold the wrong one — a work session on a link sent to the personal
 *  address, or someone else's session on a shared machine. The page says who is signed in and offers
 *  the other door BEFORE anything joins. */
export const NOT_YOU = "Not you? Use another address";

/** The line above Join: who this browser is signed in as, or "" when it cannot say. */
export function signedInLine(email?: string | null): string {
  const e = String(email || "").trim();
  return e ? `Signed in as ${e}` : "";
}

/** A refusal the other address may fix: offer "Not you?" with it. */
export function notYouHelps(kind: RefusalKind, signedIn: boolean): boolean {
  return signedIn && kind === "wrong-address";
}
