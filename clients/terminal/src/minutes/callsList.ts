/** THE NAVIGATOR'S CALLS — which calls the panel lists, in what order, and what a filter matches.
 *
 *  Founder, 2026-10-11, on the file panel: *"we probably want to have a list of calls here that will
 *  open transcripts"*.
 *
 *  ONE FRONT DOOR. The rows are the terminal's meetings store (`surfaces/liveMeetings`), which is
 *  `GET /api/meetings` and nothing else: the meetings the reader owns plus those shared with them,
 *  exactly as meeting-api authorises the meeting list. This module never fetches a meeting by any
 *  other route, so a call this reader may not see cannot reach the panel — it is not in the input.
 *  The one extra read, `>` content search, goes to `GET /api/transcripts/search` (owner-scoped,
 *  narrower than the list) and its hits are then INTERSECTED with the list: a hit whose meeting the
 *  list does not carry is dropped, never shown.
 *
 *  Every function that decides something is pure and exported, the same split as `./navigatorApi`. */
import type { MeetingMock } from "../surfaces/meetingModel";
import { meetingPhase } from "../surfaces/meetingModel";
import { isMe } from "./shell2/me";

/** Rows rendered before "Show more". The list is already in memory; this bounds the DOM, not a fetch. */
export const CALLS_PAGE = 30;
/** Above this many calls the list is grouped by month; below it, by day. */
export const MONTH_GROUPING_AT = 40;

export interface CallRow {
  /** the meeting ROW id — what the transcript canvas opens */
  id: string;
  label: string;
  /** ISO time the list orders by: when it ran, else when it was due, else when it was requested —
   *  meeting-api's own event time, COALESCE(scheduled_at, start_time, created_at), started first */
  at?: string;
  /** false for a call whose bot never got in (no start_time): it is listed at the time it was
   *  requested, and says so, rather than under a "No date" heading */
  started: boolean;
  live: boolean;
  shared: boolean;
  /** the workspace this meeting is bound to (`data.workspace_id`), as the list carries it */
  workspaceId?: string;
}

const PLATFORM_NAMES: Record<string, string> = {
  google_meet: "Google Meet", "Google Meet": "Google Meet", teams: "Teams", zoom: "Zoom", jitsi: "Jitsi",
};

/** The platform's word ("Google Meet"), for an icon's label — never a meeting's name. */
export function platformLabel(platform?: string | null): string {
  const p = String(platform ?? "").trim();
  if (!p) return "Call";
  if (p === "voice_note") return "Voice note";
  return PLATFORM_NAMES[p] ?? p.charAt(0).toUpperCase() + p.slice(1);
}

/** The minted placeholders a meeting carries before anyone named it. */
const PLACEHOLDER = /^(google meet|zoom|microsoft teams|teams|jitsi|live|voice note|untitled)?\s*(meeting|call)?$/i;
/** A chat that has no name of its own yet ("New chat", "Chat · 12:49 PM") names nothing. */
const CHAT_PLACEHOLDER = /^(new chat|untitled( chat)?|chat(\s*·.*)?)$/i;
/** A voice that is nobody in particular — a speaker label, a bot, the room. */
const NOBODY = /^(speaker\s*[a-z]?\d*|unknown( speaker)?|unidentified( speaker)?|guest|participant\s*\d*|vexa( bot)?|.*notetaker|.*\bbot)$/i;

/** Is this a person somebody could be — not "Speaker 1", "Unknown" or a bot? (founder, 2026-10-15:
 *  "Speaker means no speaker identified, so no need to put it in the header"). */
export function isIdentifiedPerson(name?: string | null): boolean {
  const n = String(name ?? "").trim();
  return !!n && !NOBODY.test(n);
}

type Nameable = Pick<MeetingMock, "id" | "title_custom" | "platform" | "native_id"> & Partial<Pick<MeetingMock, "start_time" | "scheduled_at" | "created_at" | "call_with">> & {
  title?: string; participants?: { name: string }[];
};

/** Text that is an id or a platform key, not a name (founder 2026-10-15: "humans get crazy with
 *  these names"): `vexa_call`, a Meet code, `call-21ca…`, `pchat-…`, a Zoom number, `#41`. */
const RAW_PATTERNS: RegExp[] = [
  // a link and nothing else (founder, live: a chat and its meeting titled
  // "https://teams.microsoft.com/l/meetup-join/19%3a…") — the address is not a name
  /^\s*(?:https?:\/\/|www\.)\S+\s*$/i,
  /\b(?:google_meet|vexa_call|voice_note|ms_teams)\b/i,
  /\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b/,
  /\b(?:call|vn|pchat|scaffold|meet|sess|session|unit|agent|flow|dm)-[0-9a-z][\w-]{4,}\b/i,
  /\bs-[0-9a-f]{8,}\b/i,
  /(?:^|\s)\d{9,12}(?:\s|$)/,
  /(?:^|\s)#\d+$/,
];
export function looksRaw(text?: string | null): boolean {
  const t = String(text ?? "").trim();
  return !t || RAW_PATTERNS.some((r) => r.test(t));
}

/** A stored sentence that quotes an id ("Vexa joined “call-0c90e2a5fcff”") reads with a name in its
 *  place — the object's name when known, else "your call" / "your meeting". */
export function readableSentence(text: string, objectName?: string | null): string {
  return String(text ?? "").replace(/[“"]([^”"]{1,120})[”"]/g, (all, inner: string) => {
    if (!looksRaw(inner)) return all;
    if (objectName && !looksRaw(objectName)) return `“${objectName}”`;
    return /\bcall-|vexa_call/i.test(inner) ? "your call" : "your meeting";
  });
}

/** A title a person gave, or a calendar's — not a placeholder and not an id. */
function realTitle(m: Nameable, t?: string | null): string {
  const s = String(t ?? "").trim();
  if (!s || PLACEHOLDER.test(s) || looksRaw(s)) return "";
  if (m.native_id && s.includes(m.native_id)) return "";
  if (/·\s*#\d+$/.test(s)) return "";
  return s;
}

/** THE ONE NAME A MEETING IS CALLED BY (UI decisions 14, founder 2026-10-15: "humans get crazy with
 *  these names"): its calendar title (or the one it was given) · else its chat's name · else the
 *  people in it ("Call with Valeriia") · else "Call with Vexa". Never a native or session id; the
 *  platform is an icon beside it, not part of it. Used by the Calls list, the meeting sidebar and the
 *  note's chip alike. */
/** A CALL WITH ONE PERSON, AS EACH OF THE TWO READS IT (founder O1, 2026-10-18). The caller's row
 *  says "Call with Megan"; Megan reads the same call as "Call with <the caller>". The topic after
 *  " · " is kept. Any other title is returned as it is. */
export function viewerCallTitle(title: string, callWith: { subject?: string; name?: string }[] | undefined,
  me: (p: { subject?: string; name?: string }) => boolean = (p) => isMe({ subject: p.subject, name: p.name })): string {
  if (!callWith || callWith.length !== 2 || !title.startsWith("Call with ")) return title;
  const first = (n?: string) => String(n ?? "").trim().split(/\s+/)[0] ?? "";
  const mine = callWith.find((p) => me(p));
  const other = callWith.find((p) => p !== mine);
  if (!mine || !other) return title;
  const named = first(mine.name);
  const head = `Call with ${named}`;
  if (!named || !(title === head || title.startsWith(`${head} · `))) return title;
  return `Call with ${first(other.name) || "them"}${title.slice(head.length)}`;
}

export function meetingDisplayName(m: Nameable, opts: { chatName?: string; people?: string[] } = {}): string {
  const own = realTitle(m, m.title_custom) || realTitle(m, m.title);
  if (own) return viewerCallTitle(own, m.call_with);
  const chat = String(opts.chatName ?? "").trim();
  if (chat && !PLACEHOLDER.test(chat) && !CHAT_PLACEHOLDER.test(chat) && !looksRaw(chat)) return chat;
  if (m.platform === "voice_note") return "Voice note";
  const seen = new Set<string>();
  const people: string[] = [];
  for (const n of [...(m.participants ?? []).map((p) => p.name), ...(opts.people ?? [])]) {
    const name = String(n ?? "").trim();
    if (!name || NOBODY.test(name) || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    people.push(name);
  }
  if (!people.length) {
    // a call known only by the link someone pasted is that call, at its time — never the address
    const link = [m.title_custom, m.title, opts.chatName].map((t) => linkCallName(String(t ?? ""), m.start_time || m.scheduled_at || m.created_at))
      .find((x) => x && x !== "Link");
    return link || "Call with Vexa";
  }
  if (people.length === 1) return `Call with ${people[0]}`;
  if (people.length === 2) return `Call with ${people[0]} and ${people[1]}`;
  return `Call with ${people[0]}, ${people[1]} and ${people.length - 2} more`;
}

const HOST_CALL: [RegExp, string][] = [[/teams\.(microsoft|live)\.com/i, "Teams call"], [/(^|\.)zoom\.(us|com)/i, "Zoom call"],
  [/meet\.google\.com/i, "Google Meet call"], [/meet\.jit\.si|jitsi/i, "Jitsi call"]];
/** "Thu 15:30" — the day and time a call is known by when it has no name */
function whenWords(at?: string | number | null): string {
  const t = typeof at === "number" ? at : at ? Date.parse(at) : NaN;
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  return `${d.toLocaleDateString([], { weekday: "short" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}
/** A link someone pasted as the first message names the call it opens, not the address:
 *  "Teams call · Thu 15:30". "" when the text is not a link. */
export function linkCallName(text: string, at?: string | number | null): string {
  const t = String(text ?? "").trim();
  if (!/^\s*(?:https?:\/\/|www\.)\S+\s*$/i.test(t)) return "";
  const kind = HOST_CALL.find(([re]) => re.test(t))?.[1] ?? "";
  if (!kind) return "Link";
  const w = whenWords(at);
  return w ? `${kind} · ${w}` : kind;
}

/** A call's name in a list — `meetingDisplayName`. */
export function callLabel(m: Nameable): string {
  return meetingDisplayName(m);
}

/** The calls the panel lists: everything with a transcript to open — live or finished. A PLANNED
 *  meeting (nothing captured yet) is not a call and has no transcript, so it is not listed here; the
 *  chats rail is where plans live. Newest first; live calls lead, because they are happening now.
 *
 *  A DELETED call is not listed either. Founder, 2026-10-11: *"deleted calls should be removed from
 *  here"*. Deleting a finished meeting erases its transcript and recordings and keeps the row as
 *  history, stamped `data.artifact_deletion` (pending, then completed); the list route still returns
 *  that row, so the stamp — read once, by `artifactsDeleted` in the store — is what drops it here.
 *  With nothing left to open, a listed deleted call could only ever show an empty transcript.
 *
 *  THE TIME. A call whose bot never got in has no `start_time`, and a call sent ad hoc has no
 *  `scheduled_at`; such a row used to sort last under a "No date" heading although meeting-api knows
 *  exactly when it was requested. It now takes `created_at`, the third term of the server's own
 *  ordering, and carries `started: false` so the row can say it never started. */
export function callRows(meetings: readonly MeetingMock[]): CallRow[] {
  const rows: CallRow[] = [];
  const seen = new Set<string>();
  for (const m of meetings) {
    if (!m?.id || seen.has(m.id)) continue;
    const phase = meetingPhase(m);
    if (phase === "prep" || m.artifacts_deleted) continue;
    seen.add(m.id);
    rows.push({
      id: m.id,
      label: callLabel(m),
      at: m.start_time ?? m.scheduled_at ?? m.created_at ?? undefined,
      started: !!m.start_time || phase === "live",
      live: phase === "live",
      shared: !!m.shared,
      workspaceId: m.workspace_id || undefined,
    });
  }
  return rows.sort((a, b) =>
    (a.live === b.live ? 0 : a.live ? -1 : 1)
    || (b.at ?? "").localeCompare(a.at ?? "")
    || Number(b.id) - Number(a.id) || b.id.localeCompare(a.id));
}

export interface CallGroup { key: string; label: string; rows: CallRow[] }

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** The heading a call sits under. Live calls sit under "Live" whatever their date. */
export function groupLabel(row: CallRow, byMonth: boolean, now: Date = new Date()): { key: string; label: string } {
  if (row.live) return { key: "live", label: "Live" };
  const d = row.at ? new Date(row.at) : null;
  if (!d || !Number.isFinite(d.getTime())) return { key: "undated", label: "No date" };
  if (byMonth) {
    return {
      key: `m:${d.getFullYear()}-${d.getMonth()}`,
      label: d.toLocaleDateString(undefined, { month: "long", year: "numeric" }),
    };
  }
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  const label = sameDay(d, now) ? "Today"
    : sameDay(d, yesterday) ? "Yesterday"
    : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
  return { key: `d:${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`, label };
}

/** Rows → consecutive groups (the rows are already ordered, so a group never splits). */
export function groupCalls(rows: readonly CallRow[], now: Date = new Date(), total: number = rows.length): CallGroup[] {
  const byMonth = total > MONTH_GROUPING_AT;
  const groups: CallGroup[] = [];
  for (const r of rows) {
    const g = groupLabel(r, byMonth, now);
    const last = groups[groups.length - 1];
    if (last && last.key === g.key) last.rows.push(r);
    else groups.push({ ...g, rows: [r] });
  }
  return groups;
}

/** The time shown on a row: the clock for a day-grouped list, the date for a month-grouped one. */
export function callWhen(row: CallRow, byMonth: boolean): string {
  if (row.live) return "live";
  const d = row.at ? new Date(row.at) : null;
  if (!d || !Number.isFinite(d.getTime())) return "";
  return byMonth
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** The filter box, applied to calls: case-insensitive substring on the label — the same rule the
 *  file filter applies to names. */
export function matchCalls(text: string, rows: readonly CallRow[]): CallRow[] {
  const needle = String(text ?? "").trim().toLowerCase();
  if (!needle) return [];
  return rows.filter((r) => r.label.toLowerCase().includes(needle));
}

/** A call's workspace, if the reader can see it in this panel. The link is the meeting's own
 *  `workspace_id`; the panel's workspaces are keyed by slug or id. A workspace the panel does not
 *  list (not readable to this reader) yields nothing — no chip, rather than a chip that cannot open. */
export function callWorkspace<W extends { key: string; slug?: string }>(row: Pick<CallRow, "workspaceId">, workspaces: readonly W[]): W | undefined {
  const id = row.workspaceId;
  if (!id) return undefined;
  return workspaces.find((w) => w.key === id || w.slug === id);
}

/** How many listed calls are bound to each listed workspace — the badge on the workspace's row. */
export function callsPerWorkspace<W extends { key: string; slug?: string }>(rows: readonly CallRow[], workspaces: readonly W[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const r of rows) {
    const ws = callWorkspace(r, workspaces);
    if (ws) out.set(ws.key, (out.get(ws.key) ?? 0) + 1);
  }
  return out;
}

// ── content search (`>`) ─────────────────────────────────────────────────────────────────────────

export interface TranscriptHit { meeting_db_id: number | string; snippet?: string; text?: string; start?: number; speaker?: string | null }
export interface ContentMatch { row: CallRow; snippet: string }

/** Hits → calls, IN THE LIST ONLY. The search endpoint is owner-scoped and the list is the reader's
 *  authorisation; a hit for a meeting the list does not carry is dropped here, so the search can
 *  only ever narrow what the panel shows, never widen it. One match per call, best hit first. */
export function contentMatches(hits: readonly TranscriptHit[], rows: readonly CallRow[]): ContentMatch[] {
  const byId = new Map(rows.map((r) => [r.id, r] as const));
  const out: ContentMatch[] = [];
  const seen = new Set<string>();
  for (const h of hits ?? []) {
    const id = String(h?.meeting_db_id ?? "");
    const row = byId.get(id);
    if (!row || seen.has(id)) continue;
    seen.add(id);
    out.push({ row, snippet: (h.snippet || h.text || "").trim() });
  }
  return out;
}

/** The one content read: the caller's own transcripts, through the session proxy. Errors read as
 *  "no answer" (`null`), never as "no matches" — the panel says which. */
export async function searchTranscripts(q: string, fetcher: typeof fetch = fetch): Promise<TranscriptHit[] | null> {
  const text = String(q ?? "").trim();
  if (!text) return [];
  try {
    const r = await fetcher(`/api/transcripts/search?q=${encodeURIComponent(text)}&limit=50`, { cache: "no-store" });
    if (!r.ok) return null;
    const body = (await r.json()) as { hits?: TranscriptHit[] };
    return Array.isArray(body?.hits) ? body.hits : [];
  } catch {
    return null;
  }
}

/** A tab's label. A call's tab (`kind: "meeting"`, path = the row id) is named by the meeting as it
 *  is NOW, so a rename reaches tabs already open; every other page keeps the label it was opened
 *  with. A meeting the store does not hold keeps its stored label too. */
export function pageLabel(pg: { kind?: string; path: string; label: string }, meetings: readonly MeetingMock[]): string {
  if (pg.kind !== "meeting") return pg.label;
  const m = meetings.find((x) => x.id === pg.path);
  return m ? callLabel(m) : pg.label;
}
