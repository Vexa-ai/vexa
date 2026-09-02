/** THE INTENT ON A CHAT TURN — what a button pressed on a page asks the agent to do (PRD decision 32).
 *
 *  A turn can now carry more than prose. "Extend" and "Create this page" are not sentences the
 *  person typed; they are ACTS on a named file, and the agent needs the file — not a paraphrase of
 *  it that it then has to parse back. So the wire carries a small typed record beside the prompt,
 *  and the server half turns it into the matching preset.
 *
 *  It lives in `surfaces/` rather than beside its buttons in `minutes/` because three layers have
 *  to agree on it — the panel that mints it, the chat that sends it, the stream that puts it on the
 *  wire — and the dependency direction runs that way (surfaces are below shells, never above).
 *
 *  F63 — AN INTENT NEVER CARRIES A GUESSED PATH. Everything below refuses rather than repairs: an
 *  empty path, a path that walks out of its mount, a range that does not describe its own selection.
 *  A malformed intent becomes `null` here and the caller sends nothing, because the failure mode
 *  this exists to prevent is the agent confidently working on a file that was never open.
 */

/** The most selected text an intent carries. Past this the selection is no longer a quotation, and
 *  the page itself — which the intent already names — is the better thing to hand the agent. */
export const SELECTION_MAX = 2000;

export type ChatIntentKind = "extend" | "create";

export interface ChatIntent {
  kind: ChatIntentKind;
  /** the workspace slug the page lives in. ABSENT = the reader's own desk (a no-slug read) — an
   *  absent slug is a RESOLVED answer here, never "we did not look". */
  workspace?: string;
  /** the path inside that workspace, from its root */
  path: string;
  /** the highlighted text, trimmed and capped. Absent = the whole page. */
  selection?: string;
  /** where that selection sits IN THE FILE SOURCE. Only ever present when it could be established
   *  exactly (see `sourceRange`); a range whose basis is ambiguous is omitted, not approximated. */
  selection_range?: { start: number; end: number };
}

const isInt = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;

/** The one door an intent comes through. Returns the intent, or `null` when it would be a guess. */
export function normalizeIntent(raw: {
  kind: ChatIntentKind;
  workspace?: string | null;
  path?: string | null;
  selection?: string | null;
  selection_range?: { start: number; end: number } | null;
}): ChatIntent | null {
  if (raw.kind !== "extend" && raw.kind !== "create") return null;
  const path = String(raw.path ?? "").trim().replace(/^\/+/, "");
  // no path, or one that walks out of its mount → nothing to act on. Same refusal `resolveView`
  // applies to a link, for the same reason.
  if (!path || path.split("/").includes("..")) return null;

  const workspace = String(raw.workspace ?? "").trim() || undefined;

  const selRaw = String(raw.selection ?? "").trim();
  const selection = selRaw ? selRaw.slice(0, SELECTION_MAX) : undefined;

  const r = raw.selection_range;
  // A RANGE WITHOUT ITS SELECTION IS NOISE, and a range that does not match the length of the text
  // it claims to locate is worse than none: it points the agent at the wrong lines with the
  // authority of a number. Both are dropped; the selection itself survives.
  const selection_range =
    selection && r && isInt(r.start) && isInt(r.end) && r.end > r.start && r.end - r.start === selRaw.length
      ? { start: r.start, end: r.end }
      : undefined;

  return { kind: raw.kind, ...(workspace ? { workspace } : {}), path, ...(selection ? { selection } : {}), ...(selection_range ? { selection_range } : {}) };
}
