/** briefNote.ts — locate a meeting's brief note in the user's OWN workspace (frame-6 flow).
 *
 *  The own-workspace brief lives as this meeting's note under `kg/entities/meeting/` (owner-ruled:
 *  no shared workspace needed for a brief). The agent names the file; the prep page has to FIND it
 *  to render the brief live while the chat writes it. Matching, strongest first:
 *    1. filename carries the meeting's native id (the chat prompt asks the agent to include it);
 *    2. filename↔title token overlap (prefix-tolerant: "tech" matches "technologies").
 *  Ties go to the lexically LATEST filename — date-prefixed series notes resolve to the newest
 *  occurrence. Returns null rather than guessing on weak overlap (fail quiet here: the page then
 *  shows the honest "No brief yet" state). */

const MEETING_DIR = "kg/entities/meeting/";

const tokenize = (s: string): string[] =>
  s
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length >= 3 && !/^\d+$/.test(t));

const tokensMatch = (a: string, b: string): boolean =>
  a === b || (a.length >= 4 && b.startsWith(a)) || (b.length >= 4 && a.startsWith(b));

export function findBriefNote(
  files: string[],
  meeting: { title?: string | null; nativeId?: string | null },
): string | null {
  const notes = files
    .filter((f) => f.startsWith(MEETING_DIR) && f.endsWith(".md") && !f.endsWith("/index.md"))
    .sort()
    .reverse(); // lexically latest first — date-prefixed names put the newest occurrence on top
  if (notes.length === 0) return null;

  const native = (meeting.nativeId ?? "").trim();
  if (native) {
    const byId = notes.find((f) => f.slice(MEETING_DIR.length).includes(native));
    if (byId) return byId;
  }

  const titleTokens = tokenize(meeting.title ?? "");
  if (titleTokens.length === 0) return null;
  const need = Math.min(2, titleTokens.length);
  let best: { file: string; score: number } | null = null;
  for (const f of notes) {
    const nameTokens = tokenize(f.slice(MEETING_DIR.length));
    const score = titleTokens.filter((t) => nameTokens.some((n) => tokensMatch(t, n))).length;
    if (score >= need && (!best || score > best.score)) best = { file: f, score };
  }
  return best?.file ?? null;
}

/** A note whose frontmatter carries `example: true` is seeded demo data, never a brief. */
export function isExampleNote(text: string): boolean {
  if (!text.startsWith("---")) return false;
  const end = text.indexOf("\n---", 3);
  const fm = end === -1 ? text.slice(0, 800) : text.slice(0, end);
  return /^example:\s*true\b/m.test(fm);
}
