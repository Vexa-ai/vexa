"use client";
/** MeetingChip — a meeting named in a reply or a page, rendered as the MEETING: its title and date.
 *
 *  Founder, 2026-10-11, on a chat reply where the people were chips and the meeting was the file
 *  `2026-10-09-1401-zoom-meeting-88410021706.md`: *"i think we need a special label for a meeting
 *  alongside people companies etc"*.
 *
 *  Three spellings name a meeting, and all three render as this chip:
 *    - a meeting link, `/?meeting=<id>` or `/?meeting=<platform>/<native id>` — the reference the
 *      agent is told to write (worker `kg_links_preamble`, the seeded workspace guide);
 *    - a link to its notes page, `kg/entities/meeting/<name>.md`, in any spelling — a markdown link,
 *      a backticked path, an absolute mount path, a `[[wikilink]]` that lands on one, a file the turn
 *      wrote;
 *    - nothing else: a meeting is never guessed from prose.
 *
 *  WHO MAY SEE THE TITLE. A meeting link is answered from the meetings list (`useLiveMeetings`, which
 *  is `GET /api/meetings` — the meetings this reader owns plus those shared with them, as meeting-api
 *  authorises the list). A notes-page link is answered by the server (`POST /api/links/resolve` →
 *  `meeting_link`), which reads the row through the one meeting access decision. Either way a meeting
 *  this reader may not see renders as PLAIN TEXT — the words the writer put there, or "a meeting" —
 *  and never a title, never a filename.
 *
 *  A click opens the meeting the way the Calls list does: `OPEN_MEETING_EVENT` with the row id. The
 *  chip names the route, not a view; whatever renders a meeting answers it. */
import { useContext, useEffect, useState, type ReactNode } from "react";
import { Icon } from "./index";
import { DocMetaContext, ENTITY_CHIP, useOpenEntity } from "./docRefs";
import { resolveLink, type ResolvedLink } from "./wsLinks";
import { OPEN_MEETING_EVENT } from "../platform";
import { ensureMeetingKnown, useLiveMeetings, useLiveMeetingsLoaded } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";

/** A notes page, as the minter names it (`meeting_note.is_note_path`): one segment, `.md`, under
 *  `kg/entities/meeting/`, and not the folder's index. Any prefix — `./`, a mount path — is allowed. */
const NOTE_TAIL = /(?:^|\/)(kg\/entities\/meeting\/[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md)$/;

/** The `kg/entities/meeting/<name>.md` a path names, or null when it names no meeting's notes. */
export function meetingNotePath(path?: string | null): string | null {
  const clean = String(path ?? "").trim().replace(/[?#].*$/, "");
  const tail = NOTE_TAIL.exec(clean)?.[1] ?? null;
  return tail && !tail.endsWith("/index.md") ? tail : null;
}

/** The meeting a `?meeting=<ref>` href names (relative or absolute), or null. */
export function meetingRefOfHref(href?: string | null): string | null {
  const m = String(href ?? "").match(/[?&]meeting=([^&#]+)/);
  if (!m) return null;
  try { return decodeURIComponent(m[1]).trim() || null; } catch { return null; }
}

const PLATFORM_NAMES: Record<string, string> = {
  google_meet: "Google Meet", "google meet": "Google Meet", teams: "Teams", zoom: "Zoom", jitsi: "Jitsi",
};
/** What a meeting is called when nobody named it: its platform. */
export function platformName(platform?: string | null): string {
  const p = String(platform ?? "").trim();
  if (!p || p.toLowerCase() === "unknown") return "Meeting";
  return PLATFORM_NAMES[p.toLowerCase()] ?? p.charAt(0).toUpperCase() + p.slice(1);
}

/** `Fri 9 Oct` — and the year only when it is not this one. Empty for no date or a date that does
 *  not parse; the chip then shows its title alone. */
export function meetingDay(at?: string | null, now: Date = new Date()): string {
  const s = String(at ?? "").trim();
  if (!s) return "";
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T12:00:00Z` : s);
  if (Number.isNaN(d.getTime())) return "";
  const opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString("en-GB", opts).replace(/,/g, "");
}

/** A meetings-list row a ref names: the row id, a native id, or `<platform>/<native>`. */
export function findMeeting(meetings: readonly MeetingMock[], ref: string): MeetingMock | undefined {
  const r = ref.trim();
  if (!r) return undefined;
  const native = r.includes("/") ? r.slice(r.indexOf("/") + 1) : r;
  return meetings.find((m) => String(m.id) === r) ?? meetings.find((m) => !!m.native_id && m.native_id === native);
}

/** The chip's two words, from a list row: the meeting's own name (a rename wins), else its
 *  platform; and the day it ran, was due, or was requested. */
function fromRow(m: MeetingMock): { title: string; at?: string } {
  const row = m as MeetingMock & { created_at?: string };
  return {
    title: m.title_custom?.trim() || platformName(m.platform),
    at: m.start_time ?? m.scheduled_at ?? row.created_at,
  };
}

type MeetingAnswer = ResolvedLink & {
  kind?: string; at?: string; platform?: string;
  meeting?: { id: string; platform?: string; native?: string } | null;
};

const meetingStyle = ENTITY_CHIP.meeting;
const chipBase = {
  display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "baseline", borderRadius: 999,
  padding: "0.5px 9px 0.5px 7px", fontSize: "0.92em", fontWeight: 500, whiteSpace: "nowrap", lineHeight: 1.45,
} as const;

/** The plain words a meeting this reader may not see renders as — what the writer put there, unless
 *  that is a path (a filename is never shown for a meeting), else "a meeting". */
function plainLabel(label: ReactNode): ReactNode {
  if (typeof label === "string" && (meetingNotePath(label) || /\.md$/i.test(label.trim()) || !label.trim())) return "a meeting";
  return label ?? "a meeting";
}

function Chip({ title, at, onOpen }: { title: string; at?: string; onOpen: () => void }) {
  const [hover, setHover] = useState(false);
  const day = meetingDay(at);
  return (
    <span role="link" data-meeting-chip="" title={`Open ${title}${day ? ` · ${day}` : ""}`} onClick={onOpen}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ ...chipBase, background: hover ? meetingStyle.bg : "var(--panel2)",
        border: `1px solid ${hover ? meetingStyle.color : "var(--line)"}`, color: meetingStyle.color, cursor: "pointer" }}>
      <Icon name="mic" size={11} style={{ opacity: 0.8 }} />
      <span>{title}</span>
      {day && <span data-meeting-day="" style={{ color: "var(--t3)", fontWeight: 400 }}>· {day}</span>}
    </span>
  );
}

/** While it resolves: the shape of a chip and none of its words. */
function Pending() {
  return (
    <span data-meeting-chip-pending="" style={{ ...chipBase, background: "var(--panel2)", border: "1px solid var(--line)", color: "var(--t3)" }}>
      <Icon name="mic" size={11} style={{ opacity: 0.4 }} />Meeting
    </span>
  );
}

const openMeeting = (ref: string) => window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref } }));

/** A meeting named by its link — `?meeting=<ref>`. */
function ByRef({ meetingRef, label }: { meetingRef: string; label?: ReactNode }) {
  const meetings = useLiveMeetings();
  const loaded = useLiveMeetingsLoaded();
  const m = findMeeting(meetings, meetingRef);
  // A row created this minute is not in the list yet: ask for it once (throttled in the store).
  useEffect(() => {
    if (loaded && !m) ensureMeetingKnown(meetingRef.includes("/") ? meetingRef.slice(meetingRef.indexOf("/") + 1) : meetingRef);
  }, [loaded, m, meetingRef]);
  if (m) {
    const { title, at } = fromRow(m);
    return <Chip title={title} at={at} onOpen={() => openMeeting(String(m.id))} />;
  }
  if (!loaded) return <Pending />;
  return <span data-meeting-plain="">{plainLabel(label)}</span>;
}

/** A meeting named by its notes page — answered by the server, under this reader's access. */
function ByNote({ path, label, slug }: { path: string; label?: ReactNode; slug?: string }) {
  const doc = useContext(DocMetaContext);
  const meta = slug !== undefined ? { ...doc, slug } : doc;
  const openEntity = useOpenEntity();
  const meetings = useLiveMeetings();
  const [answer, setAnswer] = useState<MeetingAnswer | null | undefined>(undefined);
  useEffect(() => {
    let on = true;
    void resolveLink(path, meta.slug).then((r) => { if (on) setAnswer(r as MeetingAnswer); });
    return () => { on = false; };
  }, [path, meta.slug]);
  if (answer === undefined) return <Pending />;
  if (!answer || answer.kind !== "meeting" || answer.access !== "readable") {
    return <span data-meeting-plain="">{plainLabel(label)}</span>;
  }
  const id = answer.meeting?.id;
  // The list's copy is the fresher one — a rename lands there first — so it wins when it has the row.
  const row = id ? findMeeting(meetings, id) : undefined;
  const words = row ? fromRow(row) : { title: answer.title?.trim() || platformName(answer.platform), at: answer.at };
  const notes = meetingNotePath(answer.path ?? path) ?? path;
  return <Chip title={words.title} at={words.at}
    onOpen={() => (id ? openMeeting(id) : openEntity({ path: notes, slug: meta.slug }))} />;
}

/** The meeting chip. Exactly one of `meetingRef` / `path`; `label` is the writer's own words, shown
 *  as plain text when this reader may not see the meeting. `slug` pins the workspace a notes path
 *  was found in (a `[[wikilink]]` that landed on one); the doc's own workspace applies otherwise. */
export function MeetingChip({ meetingRef, path, label, slug }: { meetingRef?: string; path?: string; label?: ReactNode; slug?: string }) {
  if (meetingRef) return <ByRef meetingRef={meetingRef} label={label} />;
  const note = meetingNotePath(path);
  if (path && note) return <ByNote path={path} label={label} slug={slug} />;
  return <>{label ?? null}</>;
}
