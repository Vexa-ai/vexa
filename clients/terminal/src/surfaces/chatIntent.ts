/** THE INTENT ON A CHAT TURN — what a button pressed on a page asks the agent to do (PRD decisions 32, 35).
 *
 *  A turn can now carry more than prose. "Extend", "Create this page", a term clicked in a
 *  transcript and the transcript's own "Highlight" are not sentences the person typed; they are
 *  ACTS on a named thing, and the agent needs the thing — not a paraphrase of it that it then has
 *  to parse back. So the wire carries a small typed record beside the prompt, and the server half
 *  (`control_plane/chat_intents.py`) turns it into the matching preset.
 *
 *  It lives in `surfaces/` rather than beside its buttons in `minutes/` because three layers have
 *  to agree on it — the panel that mints it, the chat that sends it, the stream that puts it on the
 *  wire — and the dependency direction runs that way (surfaces are below shells, never above).
 *
 *  F63 — AN INTENT NEVER CARRIES A GUESSED ANYTHING. Everything below refuses rather than repairs:
 *  an empty path, a path that walks out of its mount, a range that does not describe its own
 *  selection, a term nobody clicked, a meeting with no id. A malformed intent becomes `null` here
 *  and the caller sends nothing, because the failure mode this exists to prevent is the agent
 *  confidently working on a thing that was never in front of anybody.
 *
 *  TWO FAMILIES, ONE DOOR. Page intents (`extend` · `create`) name a FILE; meeting intents
 *  (`explore` · `highlight`) name a MEETING. They are a union rather than one wide optional-
 *  everything record so that "an explore with no term" cannot type-check — the shape is the
 *  validation, and the runtime check below only has to enforce what a type cannot.
 */

/** The most selected text an intent carries. Past this the selection is no longer a quotation, and
 *  the page itself — which the intent already names — is the better thing to hand the agent. */
export const SELECTION_MAX = 2000;

/** A term is a phrase said in a room, not a document. Anything past this is a mis-click on a
 *  paragraph, and sending it would ask the agent to research a sentence. */
export const TERM_MAX = 120;

export type ChatIntentKind = "extend" | "create" | "explore" | "highlight";

/** An act on a FILE (decision 32). Split into one interface per kind — rather than one carrying
 *  `kind: "extend" | "create"` — so the union is DISCRIMINATED all the way down and `IntentOf<K>`
 *  can narrow it. A member whose own `kind` is a union is not extractable by kind, and the symptom
 *  is a call site silently typed `never`. */
export interface PageIntentFields {
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

export interface ExtendIntent extends PageIntentFields { kind: "extend" }
export interface CreateIntent extends PageIntentFields { kind: "create" }
export type PageIntent = ExtendIntent | CreateIntent;

/** A term clicked in a transcript (decision 35.3) — "find out what this is". */
export interface ExploreIntent {
  kind: "explore";
  /** the words on the chip, exactly as they were said */
  term: string;
  /** the meeting ROW id the transcript belongs to */
  meeting: string;
  /** the segment the click landed in, when the renderer knows it. Provenance for the agent, never a
   *  join key: the gateway's rows and the live SSE do not share an id space. */
  segment?: string;
}

/** The transcript's own Highlight button (decision 35.2). Carries no words at all — it asks the
 *  chat to go and find what is worth chipping, and the person never sees the asking. */
export interface HighlightIntent {
  kind: "highlight";
  meeting: string;
  /** the cursor the LAST highlight on this meeting returned. Absent = highlight from the top.
   *  Always a value the server issued; the client never invents one. */
  since?: string;
}

export type ChatIntent = ExtendIntent | CreateIntent | ExploreIntent | HighlightIntent;

/** The intents the person must NOT see as a bubble in their conversation. Mirrors
 *  `chat_intents.SILENT_KINDS` server-side — the founder's correction on Highlight is that it is
 *  silent, and a "Highlight: …" bubble would be the product narrating its own plumbing. */
export const SILENT_KINDS: ReadonlySet<ChatIntentKind> = new Set<ChatIntentKind>(["highlight"]);

export const isPageIntent = (i: ChatIntent): i is PageIntent => i.kind === "extend" || i.kind === "create";
export const isSilent = (i: ChatIntent): boolean => SILENT_KINDS.has(i.kind);

/** The loose record a caller hands in — every field optional, because a button knows only its own. */
export type RawIntent = {
  kind: ChatIntentKind;
  workspace?: string | null;
  path?: string | null;
  selection?: string | null;
  selection_range?: { start: number; end: number } | null;
  term?: string | null;
  meeting?: string | null;
  segment?: string | null;
  since?: string | null;
};

const isInt = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;
const str = (v: unknown): string => String(v ?? "").trim();

/** The intent a given kind produces — so a caller that writes `kind: "extend"` gets a `PageIntent`
 *  back and can read `.path` off it without a cast. The union is the validation (see the header);
 *  this keeps that usable at the call site instead of pushing an `as` into every button. */
export type IntentOf<K extends ChatIntentKind> = Extract<ChatIntent, { kind: K }>;

/** The one door an intent comes through. Returns the intent, or `null` when it would be a guess. */
export function normalizeIntent<K extends ChatIntentKind>(raw: Omit<RawIntent, "kind"> & { kind: K }): IntentOf<K> | null;
export function normalizeIntent(raw: RawIntent): ChatIntent | null;
export function normalizeIntent(raw: RawIntent): ChatIntent | null {
  const kind = raw?.kind;

  if (kind === "explore") {
    // A term is capped, never truncated-and-sent: a 300-character "term" is a mis-click, and
    // trimming it to 120 would hand the agent half a sentence to research as if it were a name.
    const term = str(raw.term);
    const meeting = str(raw.meeting);
    if (!term || term.length > TERM_MAX || !meeting) return null;
    const segment = str(raw.segment);
    return { kind, term, meeting, ...(segment ? { segment } : {}) };
  }

  if (kind === "highlight") {
    const meeting = str(raw.meeting);
    if (!meeting) return null;
    const since = str(raw.since);
    return { kind, meeting, ...(since ? { since } : {}) };
  }

  if (kind !== "extend" && kind !== "create") return null;

  const path = String(raw.path ?? "").trim().replace(/^\/+/, "");
  // no path, or one that walks out of its mount → nothing to act on. Same refusal `resolveView`
  // applies to a link, for the same reason.
  if (!path || path.split("/").includes("..")) return null;

  const workspace = str(raw.workspace) || undefined;

  const selRaw = str(raw.selection);
  const selection = selRaw ? selRaw.slice(0, SELECTION_MAX) : undefined;

  const r = raw.selection_range;
  // A RANGE WITHOUT ITS SELECTION IS NOISE, and a range that does not match the length of the text
  // it claims to locate is worse than none: it points the agent at the wrong lines with the
  // authority of a number. Both are dropped; the selection itself survives.
  const selection_range =
    selection && r && isInt(r.start) && isInt(r.end) && r.end > r.start && r.end - r.start === selRaw.length
      ? { start: r.start, end: r.end }
      : undefined;

  return { kind, ...(workspace ? { workspace } : {}), path, ...(selection ? { selection } : {}), ...(selection_range ? { selection_range } : {}) };
}
