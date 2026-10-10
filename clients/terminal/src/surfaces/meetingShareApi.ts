/** meetingShareApi — the terminal's calls for sharing ONE meeting, live and after.
 *
 *  Every call goes through the same-origin proxy (`/api/<path>` → gateway, which signs the caller's
 *  identity); none names a subject. The rules live server-side:
 *    • meeting-api owns who can read a meeting (`GET/PATCH /meetings/{id}/access`, the share mint,
 *      `DELETE …/share/{grant}` and `DELETE …/viewers/{user}`), owner-only;
 *    • agent-api owns who belongs to a workspace (its invites and members routes).
 *  This module only shapes requests and the link a person is sent. */
import { getJson } from "./apiClient";
import { mintInvite } from "./workspaceApi";

/** A person who opened their invite and can read the meeting. */
export interface MeetingReader { user_id: number; email: string | null; role: "viewer"; grant_id?: string | null; since?: string | null }
/** An invite nobody has opened yet. */
export interface PendingInvite { id: string; mode: "restricted"; emails: string[]; created_at?: string | null; expires_at?: string | null }
/** A sign-in link anyone may open, and how many people came in through it. */
export interface ShareLink { id: string; mode: "open"; created_at?: string | null; expires_at?: string | null; joined: number }
export interface MeetingAccess {
  meeting_id: number;
  people: MeetingReader[];
  invites: PendingInvite[];
  links: ShareLink[];
  workspace_id: string | null;
  /** may the people this meeting is shared with play and download its recording */
  recording: boolean;
}

/** How long an invite or a link stays redeemable. The server clamps to its own bounds (30 days). */
export const INVITE_TTL_SEC = 30 * 86400;
export const LINK_TTL_SEC = 7 * 86400;

const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});

export function getMeetingAccess(meetingId: string | number): Promise<MeetingAccess> {
  return getJson(`/api/meetings/${encodeURIComponent(String(meetingId))}/access`);
}

export function setRecordingShared(meetingId: string | number, recording: boolean): Promise<MeetingAccess> {
  return getJson(`/api/meetings/${encodeURIComponent(String(meetingId))}/access`, json("PATCH", { recording }));
}

export function revokeShare(meetingId: string | number, grantId: string): Promise<MeetingAccess> {
  return getJson(`/api/meetings/${encodeURIComponent(String(meetingId))}/share/${encodeURIComponent(grantId)}`, { method: "DELETE" });
}

export function removeReader(meetingId: string | number, userId: number): Promise<MeetingAccess> {
  return getJson(`/api/meetings/${encodeURIComponent(String(meetingId))}/viewers/${encodeURIComponent(String(userId))}`, { method: "DELETE" });
}

interface Minted { id: string; token: string; mode: string; expires_at: string; notified?: Record<string, boolean> }

function mintShare(meetingId: string | number, body: {
  mode: "open" | "restricted"; allowed_emails?: string[]; expires_in_sec: number; notify?: boolean; workspace_invite?: string;
}): Promise<Minted> {
  return getJson(`/api/meetings/${encodeURIComponent(String(meetingId))}/share`, json("POST", body));
}

/** Split what a person typed into addresses. Anything that does not look like one is returned
 *  separately so the dialog can say which entry it refused, instead of dropping it. */
export function parseEmails(input: string): { emails: string[]; invalid: string[] } {
  const parts = input.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  const emails: string[] = [], invalid: string[] = [];
  for (const p of parts) {
    const e = p.toLowerCase();
    if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) { if (!emails.includes(e)) emails.push(e); }
    else invalid.push(p);
  }
  return { emails, invalid };
}

/** The address a person opens. They sign in first; the terminal redeems `tshare` (and shows the
 *  workspace consent for `invite`) — the existing, reviewed arrival path (`app/App.tsx` InviteGate). */
export function shareUrl(origin: string, tshare: string, workspaceInvite?: string): string {
  const p = new URLSearchParams();
  p.set("tshare", tshare);
  if (workspaceInvite) p.set("invite", workspaceInvite);
  return `${origin}/?${p.toString()}`;
}

export type WorkspaceGrant = "none" | "viewer" | "contributor";

/** `mailed`: the server handed the invite mail to the mail service (true), or could not (false) — then
 *  the copied link is the only way it reaches them, and the dialog says so. */
export interface InviteResult { email: string; url?: string; mailed?: boolean; error?: unknown }

/** Invite each address to the meeting — one invite per person, restricted to that address so a
 *  forwarded link admits nobody else — and, if asked, to the meeting's workspace with the chosen role,
 *  bundled into the same link. The server mails each person their link (`notify`); the link is also
 *  returned for copying. Each address succeeds or fails on its own; the caller shows both. */
export async function inviteToMeeting(opts: {
  meetingId: string | number; emails: string[]; workspaceId?: string | null; workspaceRole: WorkspaceGrant; origin: string;
}): Promise<InviteResult[]> {
  const out: InviteResult[] = [];
  for (const email of opts.emails) {
    try {
      // The workspace invite first, so the ONE mail the server sends carries both.
      let ws: string | undefined;
      if (opts.workspaceId && opts.workspaceRole !== "none") {
        const inv = await mintInvite({ workspace_id: opts.workspaceId, role: opts.workspaceRole, mode: "restricted",
          allowed_emails: [email], max_uses: 1, expires_in_sec: INVITE_TTL_SEC });
        ws = inv.token;
      }
      const share = await mintShare(opts.meetingId, { mode: "restricted", allowed_emails: [email],
        expires_in_sec: INVITE_TTL_SEC, notify: true, ...(ws ? { workspace_invite: ws } : {}) });
      out.push({ email, url: shareUrl(opts.origin, share.token, ws), mailed: share.notified?.[email] === true });
    } catch (error) {
      out.push({ email, error });
    }
  }
  return out;
}

/** A link anyone may open after signing in. Revocable: turning it off removes whoever came in by it. */
export async function createShareLink(meetingId: string | number, origin: string): Promise<string> {
  const share = await mintShare(meetingId, { mode: "open", expires_in_sec: LINK_TTL_SEC });
  return shareUrl(origin, share.token);
}

/** "Shared with N" — the people who can read an owner's meeting, read off the meetings list row
 *  (`data.transcript_viewers`, which the list only ships to the owner). */
export function sharedWithCount(data: { transcript_viewers?: unknown } | null | undefined): number {
  const v = data?.transcript_viewers;
  return Array.isArray(v) ? v.length : 0;
}
