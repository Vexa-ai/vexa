"use client";
/** THE MEETING VIEW'S HEADER — one row of who/when, one row of acts (#1783).
 *
 *  Founder, 2026-10-14, right after a real call: the page showed a raw path, "Update notes" twice,
 *  "Send bot again", "Export", "…" and a "Google Meet meeting" title — no playback, no delete, no
 *  participants. So the header says, in this order:
 *
 *    · the meeting's REAL name (the row's title — a calendar's, or one the person gave it), never the
 *      platform's placeholder;
 *    · when it was, how long it ran, who was there (initials + names), and whether it is live;
 *    · the acts a person reaches for: **Share** · **Update notes** (the one
 *      place that button lives) · **Delete** (or **Stop bot** while it runs) — and everything else
 *      (rename, language, export, send the bot again) behind ONE "…".
 *
 *  Every act calls what already exists: the player the transcript seeks, the share dialog, the
 *  Extend act on the meeting's page, the meeting delete route. Nothing here is a new backend. */
import { PersonChip } from "../minutes/shell2/PersonChip";
import { useEffect, useState, type ReactNode } from "react";
import { Wikilink } from "../ui-kit/docLinks";
import { resolveLink } from "../ui-kit/wsLinks";
import { Mic, MoreHorizontal, Video } from "lucide-react";
import { isIdentifiedPerson, meetingDisplayName, platformLabel } from "../minutes/callsList";
import type { MeetingMock, Participant } from "../surfaces/meetingModel";
import { refreshMeetings } from "../surfaces/liveMeetings";
import { MeetingShareButton } from "../surfaces/MeetingShare";
import { ExportMeetingButton, exportable } from "../surfaces/MeetingBundleActions";
import { MeetingLanguageControl, useCanChangeLanguage } from "../surfaces/TranscriptionLanguagePicker";
import { CallName } from "../minutes/CallName";
import { HAPPENING, isVexaCall, meetingStatusOf, sendBotAgain, stopBot } from "../minutes/meetingActions";
import { deleteMeetingData, useMeetingDeletedHere } from "../minutes/meetingDeletion";
import { ContextMenu } from "../ui-kit/ContextMenu";
import { ConfirmDialog, IconButton } from "../ui-kit";

/** The placeholder names a page is minted with before anyone named the meeting. */
const GENERIC = /^(google meet|zoom|microsoft teams|teams|jitsi|live|voice note)?\s*meeting$/i;

/** The meeting's name — `meetingDisplayName` (UI decisions 14), with the note's own heading as the
 *  chat-name tier when the meeting has no title. Never a path, never an id. */
export function meetingName(m: MeetingMock | undefined, pageTitle = "", speakers: string[] = [], chat = ""): string {
  const page = pageTitle.trim();
  const fromPage = page && !GENERIC.test(page) && !(m?.native_id && page.includes(m.native_id)) ? page : "";
  const chatName = chat.trim() || fromPage;
  if (!m) return chatName || "Meeting";
  return meetingDisplayName(m, { chatName, people: speakers });
}

const ms = (iso?: string) => { const t = Date.parse(iso ?? ""); return Number.isFinite(t) ? t : NaN; };

/** "37 min" / "1 h 05 min" — how long it ran, or how long it has been running. */
export function durationWords(startIso?: string, endIso?: string, now: number = Date.now()): string {
  const a = ms(startIso);
  if (!Number.isFinite(a)) return "";
  const b = Number.isFinite(ms(endIso)) ? ms(endIso) : now;
  const mins = Math.max(0, Math.round((b - a) / 60000));
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")} min`;
}

/** The status word beside the name: Live while the bot is in, the bot's own word while it gets
 *  there, Ended after. */
export function statusWord(m: MeetingMock): { word: string; tone: "live" | "pending" | "ended" | "planned" } {
  const s = meetingStatusOf(m);
  if (m.platform === "voice_note") {
    if (s === "active") return { word: "Recording", tone: "live" };
    if (HAPPENING.has(s) || s === "stopping") return { word: s === "stopping" ? "Saving" : "Starting", tone: "pending" };
    return { word: "Ended", tone: "ended" };
  }
  if (s === "active") return { word: "Live", tone: "live" };
  if (s === "awaiting_admission") return { word: "Waiting to be let in", tone: "pending" };
  if (s === "needs_help") return { word: "Bot needs help", tone: "pending" };
  if (s === "requested" || s === "joining") return { word: "Joining", tone: "pending" };
  if (s === "stopping") return { word: "Saving", tone: "pending" };
  if (s === "scheduled" || s === "idle") return { word: "Planned", tone: "planned" };
  return { word: "Ended", tone: "ended" };
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

/** Who was there: the speakers the meeting saw, else the people the calendar invited. */
export function peopleOf(m: MeetingMock, speakers: string[] = []): Participant[] {
  const seen = new Set<string>();
  const out: Participant[] = [];
  const add = (name: string, role = "", email = "") => {
    const n = name.trim();
    // only people somebody identified: a speaker label is nobody, and is not shown as one
    if (!isIdentifiedPerson(n) || seen.has(n.toLowerCase())) return;
    seen.add(n.toLowerCase());
    out.push({ name: n, role, initials: initials(n), ...(email ? { email } : {}) });
  };
  for (const p of m.participants ?? []) add(p.name, p.role);
  for (const s of speakers) add(s);
  if (!out.length) for (const a of m.attendees ?? []) add(a.name || a.email.split("@")[0], "", a.email);
  return out;
}

/** ONE PERSON IN THE HEADER (UI decisions 13): a person with a page is that page's entity chip —
 *  the hover card, a click opens the page; anyone else is initials and a name. Resolved by the server
 *  (`/api/links/resolve`), which answers only for pages this reader may open. */
function Person({ p }: { p: Participant }) {
  const [entity, setEntity] = useState(false);
  useEffect(() => {
    let on = true;
    void resolveLink(p.name).then((r) => {
      if (on) setEntity(r.access === "readable" && !r.missing && !!r.path && /(^|\/)kg\/entities\/(person|company|contact)s?\//.test(r.path));
    }).catch(() => {});
    return () => { on = false; };
  }, [p.name]);
  // EVERY PERSON OPENS THEIR PROFILE (UI 7mm, founder 2026-10-18: these names were unclickable); one
  // with a knowledge page keeps that page's chip inside the same door
  return <span data-meeting-person-chip={entity ? "" : undefined} className="vx-mvh-person" title={p.name}>
    <PersonChip person={{ name: p.name, email: p.email }}>
      {entity ? <Wikilink title={p.name} /> : <><span aria-hidden className="vx-mvh-avatar">{p.initials}</span>
        <span className="vx-mvh-name">{p.name}</span></>}
    </PersonChip>
  </span>;
}

function People({ people }: { people: Participant[] }) {
  if (!people.length) return null;
  const shown = people.slice(0, 5);
  const more = people.length - shown.length;
  return <span data-meeting-people className="vx-mvh-people" aria-label={`${people.length} ${people.length === 1 ? "person" : "people"}: ${people.map((p) => p.name).join(", ")}`}>
    {shown.map((p) => <Person key={p.name} p={p} />)}
    {more > 0 && <span className="vx-mvh-more">+{more}</span>}
  </span>;
}

type Act = { id: "edit-notes" | "rename" | "export" | "send-again" | "stop-bot" | "delete" | "language"; label: string; danger?: boolean };

export function MeetingViewHeader({ meeting: m, pageTitle = "", chatName = "", speakers = [], notesAct, onEditNotes, children }: {
  meeting: MeetingMock | undefined;
  /** the page's own heading — used only when the meeting has no name of its own */
  pageTitle?: string;
  /** the name of the chat bound to the meeting — the second tier of its name (UI decisions 14) */
  chatName?: string;
  /** the speakers the transcript saw, for "who was there" when the row lists nobody */
  speakers?: string[];
  /** the view's Update/Create notes control — the one place it lives */
  notesAct?: ReactNode;
  /** "…" → Edit notes: the note is edited the way any page is, from "…", never by a click on it */
  onEditNotes?: () => void;
  children?: ReactNode;
}) {
  const deletedHere = useMeetingDeletedHere(m?.id ?? "");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [langEditing, setLangEditing] = useState(false);
  const canLang = useCanChangeLanguage(m ?? ({} as MeetingMock));
  const name = meetingName(m, pageTitle, speakers, chatName);
  if (!m) {
    return <div className="vx-mvh"><h1 data-doc-name className="vx-mvh-title">{name}</h1>{children}</div>;
  }
  const status = statusWord(m);
  const happening = HAPPENING.has(meetingStatusOf(m));
  const owner = !m.shared;
  const start = m.start_time || m.scheduled_at || m.created_at;
  const when = Number.isFinite(ms(start)) ? new Intl.DateTimeFormat(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(new Date(ms(start))) : "";
  const duration = m.start_time ? durationWords(m.start_time, happening ? undefined : m.end_time) : "";
  const people = peopleOf(m, speakers);
  const canDelete = owner && !happening && !m.artifacts_deleted && !deletedHere && meetingStatusOf(m) !== "stopping";
  const voice = m.platform === "voice_note";
  const rest: Act[] = [];
  if (onEditNotes) rest.push({ id: "edit-notes", label: "Edit notes" });
  if (owner) rest.push({ id: "rename", label: "Rename…" });
  if (exportable(m)) rest.push({ id: "export", label: "Export" });
  // A Vexa call's transcriber starts and ends with the call: no Send again, no Stop bot.
  const call = isVexaCall(m);
  if (owner && !happening && !voice && !call && m.native_id && meetingStatusOf(m) !== "stopping") rest.push({ id: "send-again", label: "Send bot again" });
  // STOP BOT AND DELETE ARE INSIDE "…" (founder, UI lab 2026-10-14): the top row is Share ·
  // Update notes; the consequential acts are one click further.
  if (meetingStatusOf(m) === "active" && !voice && canLang && m.native_id) rest.push({ id: "language", label: "Change language…" });
  if (happening && !voice && !call && owner && m.native_id) rest.push({ id: "stop-bot", label: busy === "stop-bot" ? "Stopping…" : "Stop bot" });
  if (canDelete) rest.push({ id: "delete", label: "Delete…", danger: true });

  async function run(id: Act["id"]) {
    setError("");
    if (id === "edit-notes") { onEditNotes?.(); return; }
    if (id === "delete") { setDeleting(true); return; }
    if (id === "language") { setLangEditing(true); return; }
    if (id === "rename") { (document.querySelector(`[data-meeting-view="${m!.id}"] button[data-call-name]`) as HTMLElement | null)?.click(); return; }
    if (id === "export") { setExporting(true); return; }
    setBusy(id);
    try {
      if (id === "stop-bot") await stopBot(m!);
      if (id === "send-again") await sendBotAgain(m!);
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't do that. Try again."); }
    finally { setBusy(null); }
  }
  async function remove() {
    setBusy("delete"); setError(""); setWaiting(false);
    try { await deleteMeetingData(m!.id, { onWaiting: () => setWaiting(true), native: m!.native_id }); setDeleting(false); refreshMeetings(); }
    catch (e) { setError(e instanceof Error ? e.message : "Couldn't delete. Try again."); }
    finally { setBusy(null); setWaiting(false); }
  }

  return <div className="vx-mvh" data-meeting-header>
    <div className="vx-mvh-top">
      <h1 data-doc-name className="vx-mvh-title"><CallName meeting={m} label={name} /></h1>
      <span data-meeting-status data-tone={status.tone} className="vx-mvh-status">
        {status.tone === "live" && <span aria-hidden className="vx-mv-live-dot" />}{status.word}
      </span>
    </div>
    <div data-meeting-metadata className="vx-mvh-meta">
      {/* the platform is an ICON (UI decisions 14), named for screen readers and on hover */}
      <span data-meeting-platform role="img" aria-label={platformLabel(m.platform)} title={platformLabel(m.platform)} className="vx-mvh-platform">
        {voice ? <Mic size={13} aria-hidden /> : <Video size={13} aria-hidden />}
      </span>
      {[when, duration].filter(Boolean).join(" · ")}
      <People people={people} />
      {!langEditing && <MeetingLanguageControl key={m.id} meeting={m} compact />}
    </div>
    {langEditing && <MeetingLanguageControl key={`${m.id}-edit`} meeting={m} compact editing onDone={() => setLangEditing(false)} />}
    {!deletedHere && <div data-meeting-actions role="toolbar" aria-label={voice ? "Voice note actions" : "Meeting actions"} className="vx-mvh-acts">
      {/* NO "Play recording" BUTTON HERE (founder, app.dev 2026-10-14: "just doing nothing —
          duplicating the real small thing — remove this"). It only toggled the inline player under
          this row, which is the one playback control: play/pause, time, scrubber. */}
      <MeetingShareButton meeting={m} />
      {notesAct}
      {exporting && <ExportMeetingButton meetingId={m.id} autoStart onDone={() => setExporting(false)} />}
      {rest.length > 0 && <IconButton label={voice ? "More voice note actions" : "More meeting actions"} size="xs" aria-haspopup="menu" aria-expanded={!!menu}
        onClick={(e) => { if (menu) { setMenu(null); return; } const b = e.currentTarget.getBoundingClientRect(); setMenu({ x: b.right - 188, y: b.bottom + 4 }); }}>
        <MoreHorizontal size={14} strokeWidth={1.75} />
      </IconButton>}
      {menu && <ContextMenu x={menu.x} y={menu.y} label={voice ? "Voice note actions" : "Meeting actions"} onClose={() => setMenu(null)}
        items={rest.map((a) => ({ id: a.id, label: a.label, ...(a.danger ? { danger: true } : {}), onSelect: () => { setMenu(null); void run(a.id); } }))} />}
      {error && <span role="alert" className="vx-mv-meta vx-mv-error">{error}</span>}
    </div>}
    {children}
    <ConfirmDialog open={deleting} busy={busy === "delete"} title={voice ? "Delete this voice note?" : "Delete this meeting?"}
      confirmLabel={voice ? "Delete voice note" : "Delete meeting"} typeToConfirm="delete"
      consequence={<>
        Its transcript, notes and recording are deleted for good, on every device. Pages saved in a workspace stay.
        {waiting && <span role="status" className="vx-mc-note vx-mc-wait">Waiting for the recording to finish saving…</span>}
      </>}
      onCancel={() => { if (busy !== "delete") setDeleting(false); }}
      onConfirm={() => { if (busy !== "delete") void remove(); }} />
  </div>;
}
