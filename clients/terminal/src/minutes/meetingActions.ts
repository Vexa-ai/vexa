/** A MEETING'S ACTIONS, ONE LIST (#1783, founder: "where is stop bot, delete, etc?").
 *
 *  The meeting / voice-note page header and the rail's meeting row menu draw the same set, from
 *  here, so the two cannot disagree about what a meeting offers in which state:
 *
 *  - while it is happening: **Stop bot** (a meeting) · **Pause/Resume** and **Stop** (a voice note
 *    this tab is recording);
 *  - always: **Rename**, **Change language** (while live, where the language can change), **Share**,
 *    **Export**, **Delete** (not while it is happening; a meeting still saving is waited for);
 *  - after it ends: **Update notes** (creates the notes page if there is none, then writes it from
 *    the transcript in the chat) · **Send bot again** to the same link (a meeting, not a voice note).
 *
 *  Every act calls a route that already exists; nothing here is a new backend. */
import type { MeetingMock } from "../surfaces/meetingModel";
import { platformSlug } from "../surfaces/transcriptionLanguage";
import { defaultBotName } from "../surfaces/defaultBotName";

export type MeetingActId =
  | "stop-bot" | "pause" | "resume" | "stop-note"
  | "rename" | "language" | "share" | "export" | "delete"
  | "notes" | "send-again";

export type MeetingAct = { id: MeetingActId; label: string; primary?: boolean; danger?: boolean };

/** The statuses a meeting is still happening in. */
export const HAPPENING = new Set(["requested", "joining", "awaiting_admission", "needs_help", "active"]);

/** A Vexa call (`vexa_call`): its transcriber starts and ends with the call itself (founder
 *  2026-10-15, "it's our internal bot") — nobody stops it, and nobody sends it again. */
export function isVexaCall(m: Pick<MeetingMock, "platform">): boolean {
  return m.platform === "vexa_call";
}

export function meetingStatusOf(m: MeetingMock): string {
  return m.live_status || (m.status === "live" ? "active" : "completed");
}

/** What this meeting offers, in order; `primary` acts are drawn as buttons, the rest go in "…". */
export function meetingActs(m: MeetingMock, opts: {
  /** this tab is recording this voice note: `paused` says which of Pause/Resume to offer */
  recording?: { paused: boolean } | null;
  canRename?: boolean; canShare?: boolean; canExport?: boolean; canChangeLanguage?: boolean;
} = {}): MeetingAct[] {
  const status = meetingStatusOf(m);
  const voice = m.platform === "voice_note";
  const happening = HAPPENING.has(status);
  const ended = !happening && status !== "stopping";
  const out: MeetingAct[] = [];
  const call = isVexaCall(m);
  if (happening && !voice && !call && m.native_id) out.push({ id: "stop-bot", label: "Stop bot", primary: true });
  if (voice && opts.recording) {
    out.push(opts.recording.paused ? { id: "resume", label: "Resume", primary: true } : { id: "pause", label: "Pause", primary: true });
    out.push({ id: "stop-note", label: "Stop", primary: true });
  }
  if (ended && !m.shared) out.push({ id: "notes", label: "Update notes", primary: true });
  if (ended && !voice && !call && m.native_id && !m.shared) out.push({ id: "send-again", label: "Send bot again", primary: true });
  if (opts.canRename !== false) out.push({ id: "rename", label: "Rename…" });
  if (happening && opts.canChangeLanguage) out.push({ id: "language", label: "Change language…" });
  if (opts.canShare !== false) out.push({ id: "share", label: "Share…" });
  if (opts.canExport) out.push({ id: "export", label: "Export" });
  if (!happening && !m.shared && !m.artifacts_deleted) out.push({ id: "delete", label: voice ? "Delete voice note…" : "Delete meeting…", danger: true });
  return out;
}

async function ok(r: Response, what: string): Promise<void> {
  if (!r.ok) throw new Error(`Couldn't ${what}. Try again.`);
}

/** Stop the bot: the user-stop route (`DELETE /api/bots/{platform}/{native}`). */
export async function stopBot(m: MeetingMock, f: typeof fetch = fetch): Promise<void> {
  await ok(await f(`/api/bots/${platformSlug(m.platform)}/${encodeURIComponent(m.native_id as string)}`, { method: "DELETE" }), "stop the bot");
}

/** Send a bot to the same link again (`POST /api/bots`, the Send now act's body). */
export async function sendBotAgain(m: MeetingMock, f: typeof fetch = fetch): Promise<void> {
  const slug = platformSlug(m.platform);
  const native = m.native_id as string;
  await ok(await f("/api/bots", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform: slug, native_meeting_id: native,
      ...(m.meeting_url ? { meeting_url: m.meeting_url } : slug === "google_meet" ? { meeting_url: `https://meet.google.com/${native}` } : {}),
      bot_name: defaultBotName(),
    }),
  }), "send the bot");
}
