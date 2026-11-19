/** MEETING PREP, READ FROM WHAT ALREADY OWNS IT (feature `meeting_prep`; validation log O2, founder-felt
 *  2026-10-18; architecture review 2026-10-18 §1: "Prep is a read projection over existing carriers").
 *
 *  Nothing here is stored and there is no prep route. Every line of the Prep section comes from the
 *  object that owns it, read through the route the app already uses for it:
 *
 *    - the GOAL: `goals:` in the front matter of the meeting's note page (meeting-prep-conditions C6).
 *      Setting it is a write of that page — the same write the notes editor makes. The page the view
 *      reads is the person's desk copy unless the meeting's page lives in a shared workspace, in which
 *      case the goal is shared too and the section says so (never "only you" when it is not);
 *    - the PEOPLE: the meeting row's attendees (meeting-api) resolved per viewer against the people the
 *      viewer already knows (`/api/people/peers` + `/api/people/known`, merged by account and address).
 *      The sources are how the viewer knows them (met · chats · a shared workspace · their page ·
 *      email), never an identity handle as a name;
 *    - OPEN FROM LAST TIME: the open cards (crm-api's Inbox read) about the previous occurrence of this
 *      meeting — the newest earlier row with the same title;
 *    - the AGENDA: the `## Agenda` list on the meeting page (or an `agenda:` list in its front matter);
 *    - the MATERIAL: full references on the meeting page (`[[page:…]]`, `[[file:…]]`, …);
 *    - READY: three ticks derived at read time — a goal, people resolved, material — never stored. */
import { isMap, parseDocument } from "yaml";

export type PrepAttendee = { email: string; name?: string };
export type KnownLike = { key: string; subject: string | null; name: string; email: string; how: string[]; headline?: string; roles?: Record<string, string>; workspaces?: string[] };
export type PrepPerson = { name: string; email: string; subject: string | null; key: string | null; role: string; sources: string[]; known: boolean };
export type CardLike = { id: string; title: string; open?: boolean; due?: string | null; next?: string | null; waiting?: string | null; about?: { kind: string; ref: string } | null };
export type RowLike = { id: string; native_id?: string; title?: string; title_custom?: string | null; scheduled_at?: string; start_time?: string; created_at?: string; live_status?: string; attendees?: PrepAttendee[] };

function frontMatter(raw: string): { fm: string; start: number; end: number } | null {
  if (!raw.startsWith("---")) return null;
  const close = raw.indexOf("\n---", 3);
  if (close === -1) return null;
  const firstNl = raw.indexOf("\n");
  return { fm: raw.slice(firstNl + 1, close + 1), start: firstNl + 1, end: close + 1 };
}

function fmObject(raw: string): Record<string, unknown> {
  const f = frontMatter(raw ?? "");
  if (!f) return {};
  try {
    const doc = parseDocument(f.fm);
    if (doc.errors.length) return {};
    const v = doc.toJS() as unknown;
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch { return {}; }
}

/** The page's `goals:` — one line, or a list joined; "" when none. */
export function readGoal(raw: string): string {
  const g = fmObject(raw).goals;
  if (typeof g === "string") return g.trim();
  if (Array.isArray(g)) return g.filter((x) => typeof x === "string" && x.trim()).map((x) => String(x).trim()).join(" · ");
  return "";
}

/** The page with `goals:` set to `goal` — every other key, comment and the body exactly as they were.
 *  A page with no front matter gets one. */
export function writeGoal(raw: string, goal: string): string {
  const src = raw ?? "";
  const f = frontMatter(src);
  const doc = parseDocument(f ? f.fm : "");
  if (!isMap(doc.contents)) doc.contents = doc.createNode({}) as unknown as typeof doc.contents;
  doc.set("goals", goal.trim());
  const fm = doc.toString({ lineWidth: 0 });
  if (!f) return `---\n${fm}---\n\n${src}`;
  return src.slice(0, f.start) + fm + src.slice(f.end);
}

/** The body after the front matter. */
function bodyOf(raw: string): string {
  const f = frontMatter(raw ?? "");
  return f ? raw.slice(f.end).replace(/^---[ \t]*\r?\n?/, "") : (raw ?? "");
}

/** The agenda: the items of the page's `## Agenda` section, or its `agenda:` front-matter list. */
export function readAgenda(raw: string): string[] {
  const fmA = fmObject(raw).agenda;
  if (Array.isArray(fmA)) return fmA.filter((x) => typeof x === "string" && x.trim()).map((x) => String(x).trim());
  const lines = bodyOf(raw).split(/\r?\n/);
  const at = lines.findIndex((l) => /^#{1,4}\s+agenda\b/i.test(l.trim()));
  if (at === -1) return [];
  const out: string[] = [];
  for (const l of lines.slice(at + 1)) {
    if (/^#{1,4}\s/.test(l.trim())) break;
    const m = /^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$/.exec(l);
    if (m && m[1]) out.push(m[1]);
  }
  return out;
}

const REF = /\[\[(page|file|entity|card|room|chat|workspace|commit):([^\]|]+)(?:\|([^\]]*))?\]\]/g;
/** The material: every full reference to a page, file or other object written on the meeting page,
 *  once each, in page order. (A person or meeting reference is not material.) */
export function readMaterial(raw: string): string[] {
  const out: string[] = [];
  for (const m of bodyOf(raw).matchAll(REF)) if (!out.includes(m[0])) out.push(m[0]);
  return out;
}

const SOURCE_WORD: Record<string, string> = { met: "met before", chat: "chats", workspace: "shared workspace", page: "your page", mail: "email" };
/** A person's sources, in plain words, from how the viewer knows them. */
export function sourcesOf(how: readonly string[]): string[] {
  return [...new Set(how.map((h) => SOURCE_WORD[h]).filter(Boolean))];
}

const emailOf = (v: unknown) => (typeof v === "string" ? v.trim().toLowerCase() : "");
/** The meeting's people as THIS viewer can know them: each attendee once, resolved against the people
 *  the viewer already knows by address; the viewer themself left out. An attendee nobody knows yet is
 *  shown by the name the invite carries (or their address's first word), with no sources. */
export function prepPeople(attendees: readonly PrepAttendee[] | undefined, known: readonly KnownLike[], me: { email?: string | null } = {}): PrepPerson[] {
  const mine = emailOf(me.email);
  const seen = new Set<string>();
  const out: PrepPerson[] = [];
  for (const a of attendees ?? []) {
    const email = emailOf(a?.email);
    if (!email || email === mine || seen.has(email)) continue;
    seen.add(email);
    const k = known.find((p) => emailOf(p.email) === email);
    if (k) {
      const role = (k.headline || Object.values(k.roles ?? {})[0] || "").trim();
      out.push({ name: k.name, email, subject: k.subject, key: k.key, role, sources: sourcesOf(k.how ?? []), known: true });
    } else {
      const name = (a.name && !a.name.includes("@") ? a.name : email.split("@")[0].replace(/[._]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())).trim();
      out.push({ name, email, subject: null, key: null, role: "", sources: [], known: false });
    }
  }
  return out;
}

const timeOf = (r: RowLike) => Date.parse(r.scheduled_at || r.start_time || r.created_at || "") || 0;
const titleOf = (r: RowLike) => (r.title_custom || r.title || "").trim().toLowerCase();
/** The previous occurrence of this meeting: the newest row before it with the same title. */
export function previousOccurrence(rows: readonly RowLike[], row: RowLike): RowLike | null {
  const t = titleOf(row), at = timeOf(row);
  if (!t) return null;
  const earlier = rows.filter((r) => r.id !== row.id && titleOf(r) === t && timeOf(r) > 0 && timeOf(r) < (at || Date.now()));
  earlier.sort((a, b) => timeOf(b) - timeOf(a));
  return earlier[0] ?? null;
}

/** Is this card about that meeting? (`about.ref` is its row id, its native id or its full reference.) */
export function cardAboutMeeting(c: CardLike, row: RowLike): boolean {
  if (!c.about || c.about.kind !== "meeting") return false;
  const ref = String(c.about.ref ?? "").replace(/^\[\[meeting:/, "").replace(/(\|[^\]]*)?\]\]$/, "").replace(/^meeting:/, "").trim();
  return !!ref && (ref === String(row.id) || (!!row.native_id && ref === row.native_id));
}

/** Open cards about the previous occurrence. */
export function openFromLast(cards: readonly CardLike[], prev: RowLike | null): CardLike[] {
  if (!prev) return [];
  return cards.filter((c) => c.open !== false && cardAboutMeeting(c, prev));
}

export type Readiness = { goal: boolean; people: boolean; material: boolean; ready: boolean };
export function readiness(goal: string, people: readonly PrepPerson[], material: readonly string[]): Readiness {
  const r = { goal: !!goal.trim(), people: people.some((p) => p.known), material: material.length > 0 };
  return { ...r, ready: r.goal && r.people && r.material };
}

/** "in 30 min" / "in 2 h" / "Fri 13:00" — when a planned meeting starts, as Home says it. */
export function startsIn(at: number, now: number = Date.now()): string {
  const min = Math.round((at - now) / 60_000);
  if (min <= 0) return "now";
  if (min < 60) return `in ${min} min`;
  if (min < 6 * 60) return `in ${Math.round(min / 60)} h`;
  const d = new Date(at);
  const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.toLocaleDateString(undefined, { weekday: "short" })} ${hh}:${mm}`;
}
