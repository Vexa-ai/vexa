/** WHAT A HOVER CARD SAYS ABOUT AN ENTITY (founder, 2026-10-15: "this takes too much space — make it
 *  small and only on hover"; then "so this should be some things on hover"). At most three short
 *  lines under the name, each omitted when the page has nothing for it:
 *    1. role · organisation (a company: industry · place)
 *    2. how you are connected — the most recent touch ("Last met Oct 7 · AI Tinkerers call")
 *    3. the page's first sentence
 *  Never the page's TYPE/ID/TITLE/ALIASES rows. Pure: the page's markdown in, the lines out. */

const ROLE = ["role", "position", "job_title", "headline"];
const ORG = ["company", "organisation", "organization", "org", "employer", "affiliation"];
const KIND_OF_ORG = ["industry", "sector", "category"];
const PLACE = ["location", "hq", "city", "country", "based"];
const WHEN = ["last_met", "last_meeting_date", "last_contact", "last_contacted", "last_touch", "lastcontactat", "last_seen", "met"];
const WHAT = ["last_meeting", "last_touch_note", "met_via", "context"];
const WHERE = ["via", "connected", "room", "community"];
const TOUCH_HEADINGS = /^#{1,4}\s*(interactions?|meetings?|timeline|history|contact(?: history)?|touches|log|notes)\b/i;

const cap = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).replace(/[\s,;:·—-]+$/, "")}…` : s);

/** The value as words: no quotes, no [[wikilink]] brackets, no markdown marks, no list syntax. */
function words(v: unknown): string {
  let s = String(v ?? "").trim();
  if (s.startsWith("[") && s.endsWith("]") && !s.startsWith("[[")) s = s.slice(1, -1).split(",")[0] ?? "";
  return s.replace(/^["']|["']$/g, "").replace(/\[\[([^\]|]*)\|?([^\]]*)\]\]/g, (_m, a, b) => b || a)
    .replace(/[*_`]/g, "").replace(/\s+/g, " ").trim();
}

export function frontMatter(md: string): { fm: Record<string, string>; body: string } {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(md);
  const fm: Record<string, string> = {};
  if (!m) return { fm, body: md };
  for (const line of m[1].split("\n")) {
    const kv = /^([A-Za-z][\w -]{0,40}):\s*(.*)$/.exec(line);
    if (kv && kv[2].trim()) fm[kv[1].trim().toLowerCase().replace(/[\s-]+/g, "_")] = kv[2].trim();
  }
  return { fm, body: md.slice(m[0].length) };
}

const first = (fm: Record<string, string>, keys: string[]) => {
  for (const k of keys) { const v = words(fm[k]); if (v) return v; }
  return "";
};

/** "Oct 7" (this year) or "Oct 7, 2025"; "" when it is not a date. */
export function shortDate(v: string, now = Date.now()): string {
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return "";
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (isNaN(+d)) return "";
  const mon = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return +m[1] === new Date(now).getUTCFullYear() ? mon : `${mon}, ${m[1]}`;
}

/** The newest dated line under an interactions/meetings/timeline heading: [date, what]. */
function latestTouch(body: string): [string, string] | null {
  let inside = false;
  let best: [string, string] | null = null;
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (/^#{1,6}\s/.test(line)) { inside = TOUCH_HEADINGS.test(line); continue; }
    if (!inside) continue;
    const m = /(\d{4}-\d{2}-\d{2})\s*[:\-—·|]*\s*(.*)$/.exec(line.replace(/^[-*+]\s+/, ""));
    if (m && (!best || m[1] > best[0])) best = [m[1], words(m[2]).replace(/^[-—·:|\s]+/, "")];
  }
  return best;
}

/** THE PAGE'S FIRST PROSE SENTENCE (founder, live: the line showed raw relation bullets — "- Imad's
 *  agent-built multiplayer game — building - … — works on - 2026-10-15 how-iich-wse — …"). Only a
 *  paragraph of plain prose counts: never a list, a table, a heading, a callout, a code block, a
 *  relation line ("X — works on"), or anything carrying a date, a slug or an id. No prose → "". */
export function proseSentence(body: string): string {
  const paras = String(body ?? "").replace(/^---\n[\s\S]*?\n---\n?/, "").replace(/```[\s\S]*?```/g, "").split(/\n\s*\n/);
  for (const raw of paras) {
    const p = raw.trim();
    if (!p) continue;
    const lines = p.split("\n");
    if (lines.some((l) => /^\s*([-*+]\s|\d+[.)]\s|#|\||<|>|!\[|\w[\w ]{0,30}:\s*\S)/.test(l))) continue;
    const text = words(p.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\n/g, " "));
    if (!text || /\d{4}-\d{2}-\d{2}/.test(text) || /\b[a-z0-9]+(?:-[a-z0-9]+){2,}\b/.test(text)
        || /\s[—–-]\s.*\s[—–-]\s/.test(text) || /\b(?:pchat|meet|call|vn|s)-[0-9a-z]{5,}\b/i.test(text)) continue;
    if (text.split(/\s+/).length < 3) continue;
    return /^(.+?[.!?])(\s|$)/.exec(text)?.[1] ?? text;
  }
  return "";
}

/** Up to three lines, in order: who they are, how you are connected, what the page says first. */
export function hoverFacts(md: string, kind?: string, now = Date.now()): string[] {
  const { fm, body } = frontMatter(String(md ?? ""));
  const k = (kind || words(fm.type)).toLowerCase();
  const org = k === "company" || k === "organization" || k === "organisation";
  const who = org
    ? [first(fm, KIND_OF_ORG), first(fm, PLACE)].filter(Boolean).join(" · ")
    : [first(fm, ROLE), first(fm, ORG)].filter(Boolean).join(" · ");
  let touch = "";
  const whenRaw = first(fm, WHEN);
  const when = shortDate(whenRaw, now);
  const what = first(fm, WHAT);
  const found = !when ? latestTouch(body) : null;
  const date = when || (found ? shortDate(found[0], now) : "");
  const note = what || (found ? found[1] : "");
  if (date) {
    const met = /\b(call|meeting|met|meetup|demo|sync|chat|coffee|lunch|dinner)\b/i.test(note) || WHEN.slice(0, 2).some((x) => fm[x]);
    touch = `${met ? "Last met" : "Last touch"} ${date}${note ? ` · ${note}` : ""}`;
  } else {
    const where = first(fm, WHERE);
    if (where) touch = /^(in|via|through|from)\b/i.test(where) ? where : `In ${where}`;
  }
  const lines = [cap(who, 60), cap(touch, 60), cap(proseSentence(body), 90)];
  return lines.filter(Boolean);
}

/** THE SHORT STORY (founder, live: "this should tell the full story, short one"): the page's own
 *  `summary:` — written by the agent, or backfilled once — else its first prose sentences, at most
 *  three and ~280 characters, ending on a sentence. "" when the page has neither. */
export const STORY_MAX = 280;
export function pageSummary(md: string): string {
  const { fm } = frontMatter(String(md ?? ""));
  let v = String(fm.summary ?? "").trim();
  if (v.startsWith('"') && v.endsWith('"')) { try { v = JSON.parse(v); } catch { v = v.slice(1, -1); } }
  return storyClip(words(v));
}
export function storyClip(t: string): string {
  const s = t.replace(/\s+/g, " ").trim();
  if (s.length <= STORY_MAX) return s;
  const cut = s.slice(0, STORY_MAX);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
  return end > 60 ? cut.slice(0, end + 1) : `${cut.slice(0, cut.lastIndexOf(" ")).replace(/[,;:]$/, "")}…`;
}
export function hoverStory(md: string): string {
  const own = pageSummary(md);
  if (own) return own;
  const { body } = frontMatter(String(md ?? ""));
  const out: string[] = [];
  let rest = body;
  for (let i = 0; i < 3; i++) {
    const s = proseSentence(rest);
    if (!s) break;
    if ((out.join(" ") + " " + s).trim().length > STORY_MAX) break;
    out.push(s);
    rest = rest.slice(rest.indexOf(s.slice(0, Math.min(20, s.length))) + s.length);
  }
  return storyClip(out.join(" "));
}
