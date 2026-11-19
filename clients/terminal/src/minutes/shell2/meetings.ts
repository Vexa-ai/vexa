/** THE MEETINGS PAGE's decisions, pure (UI 7w, 7y; feature `shell2`). The rows are the terminal's
 *  meetings store (`GET /api/meetings` — what meeting-api authorises for this reader) and nothing
 *  else; this module only sorts, filters and groups them. */
import type { MeetingMock } from "../../surfaces/meetingModel";
import { meetingPhase } from "../../surfaces/meetingModel";
import { callLabel, isIdentifiedPerson } from "../callsList";
import { formatDate } from "../../ui-kit";

export type Filters = { q: string; workspace: string; person: string };
export const NO_FILTERS: Filters = { q: "", workspace: "all", person: "any" };

/** Who was in it: identified people only (no "Speaker 1", no bots), attendees' names as a fallback. */
export function peopleOf(m: MeetingMock): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const n of [...(m.participants ?? []).map((p) => p.name), ...(m.attendees ?? []).map((a) => a.name ?? "")]) {
    const s = String(n ?? "").trim();
    if (!s || !isIdentifiedPerson(s) || seen.has(s.toLowerCase())) continue;
    seen.add(s.toLowerCase());
    out.push(s);
  }
  return out;
}

const time = (iso?: string) => { const t = iso ? Date.parse(iso) : NaN; return Number.isFinite(t) ? t : 0; };

/** Live now: the bot is in, or on its way in. */
export function liveMeetings(ms: readonly MeetingMock[]): MeetingMock[] {
  return ms.filter((m) => meetingPhase(m) === "live" && !m.artifacts_deleted);
}

/** Upcoming: planned meetings due from now on, soonest first. */
export function upcomingMeetings(ms: readonly MeetingMock[], now = Date.now()): MeetingMock[] {
  return ms.filter((m) => meetingPhase(m) === "prep" && time(m.scheduled_at) >= now - 15 * 60_000)
    .sort((a, b) => time(a.scheduled_at) - time(b.scheduled_at));
}

/** Past: meetings that ran (or were sent and never got in), newest first; deleted ones are gone. */
export function pastMeetings(ms: readonly MeetingMock[]): MeetingMock[] {
  return ms.filter((m) => meetingPhase(m) === "post" && !m.artifacts_deleted)
    .sort((a, b) => time(b.start_time ?? b.scheduled_at ?? b.created_at) - time(a.start_time ?? a.scheduled_at ?? a.created_at));
}

export function matchMeetings(ms: readonly MeetingMock[], f: Filters, wsName: (slug: string) => string): MeetingMock[] {
  const needle = f.q.trim().toLowerCase();
  return ms.filter((m) => (f.workspace === "all" || (f.workspace === "personal" ? !m.workspace_id || m.workspace_id === "personal" : m.workspace_id === f.workspace))
    && (f.person === "any" || peopleOf(m).includes(f.person))
    && (!needle || [callLabel(m), ...peopleOf(m), m.workspace_id ? wsName(m.workspace_id) : ""].some((x) => x.toLowerCase().includes(needle))));
}

/** Minutes it ran, or null when it never started or has no end yet. */
export function durationMin(m: MeetingMock): number | null {
  const a = time(m.start_time), b = time(m.end_time);
  return a && b && b > a ? Math.max(1, Math.round((b - a) / 60_000)) : null;
}

const startOfWeek = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };

/** Week, then month groups (UI 7y): "This week", "Last week", then "October", "September 2025"… */
export function weekMonthGroup(iso: string | undefined, now = new Date()): { key: string; label: string } {
  const t = time(iso);
  if (!t) return { key: "undated", label: "No date" };
  const d = new Date(t);
  const thisWeek = startOfWeek(now).getTime();
  if (t >= thisWeek) return { key: "w0", label: "This week" };
  if (t >= thisWeek - 7 * 86_400_000) return { key: "w1", label: "Last week" };
  const sameYear = d.getFullYear() === now.getFullYear();
  return { key: `m:${d.getFullYear()}-${d.getMonth()}`, label: d.toLocaleDateString("en-US", { month: "long", ...(sameYear ? {} : { year: "numeric" }) }) };
}

export function groupPast(ms: readonly MeetingMock[], now = new Date()): { key: string; label: string; rows: MeetingMock[] }[] {
  const out: { key: string; label: string; rows: MeetingMock[] }[] = [];
  for (const m of ms) {
    const g = weekMonthGroup(m.start_time ?? m.scheduled_at ?? m.created_at, now);
    const last = out[out.length - 1];
    if (last && last.key === g.key) last.rows.push(m); else out.push({ ...g, rows: [m] });
  }
  return out;
}

/** The row's time column, short enough for 72px: the clock today, else the calendar day the one
 *  formatter gives ("Yesterday", "Thu", "Oct 2"); the full time is in the row's detail and tooltip. */
export function shortWhen(iso: string | undefined, now = new Date()): string {
  const t = time(iso);
  if (!t) return "";
  const d = new Date(t);
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (sameDay) return formatDate(d, { now });
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return formatDate(day, { now });
}
