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
 *  (`explore` · `highlight` · `extend_transcript`) name a MEETING. They are a union rather than one
 *  wide optional-everything record so that "an explore with no term" cannot type-check — the shape
 *  is the validation, and the runtime check below only has to enforce what a type cannot.
 */

/** The most selected text an intent carries. Past this the selection is no longer a quotation, and
 *  the page itself — which the intent already names — is the better thing to hand the agent. */
export const SELECTION_MAX = 2000;

/** A term is a phrase said in a room, not a document. Anything past this is a mis-click on a
 *  paragraph, and sending it would ask the agent to research a sentence. */
export const TERM_MAX = 120;

/** The most a person's own INSTRUCTION line carries (Vexa-ai/vexa#1593). It is one line typed into
 *  a one-line field — the cap is here so a paste cannot turn the WHAT into a document, and so the
 *  bound is stated on the wire rather than left to whatever the input happened to accept. */
export const INSTRUCTION_MAX = 400;

/** The most a NAMED MEMBER carries (Vexa-ai/vexa#1632). An email address plus a display label, and
 *  never a paragraph: a member act names ONE person the roster already rendered, so anything past
 *  this is a paste rather than a name, and the roster is where the value came from. */
export const MEMBER_MAX = 320;

export type ChatIntentKind = "extend" | "create" | "explore" | "highlight" | "extend_transcript"
  | "policies_wizard" | "member_add" | "member_role" | "member_remove" | "flow_author";

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
  /** THE PERSON'S OWN LINE (Vexa-ai/vexa#1593) — what to do with it, typed on the button, VERBATIM.
   *  The selection is the WHERE; this is the WHAT. Absent = they pressed the button and typed
   *  nothing, which is the act exactly as it behaved before this field existed.
   *
   *  It is the only field on an intent that is a person's own text rather than a fact about the
   *  screen, and that is why it is safe: the words are theirs, addressed to their own agent, the
   *  same capability the composer two panels away already gives them. Everything else on this
   *  record stays a NAME or a resolved slot, for the reason the header states. */
  instruction?: string;
  /** THE MEETING THIS PAGE IS (Vexa-ai/vexa#1598). Present only when the open page DECLARES a
   *  transcript widget — `<!-- vexa:transcript meeting=147 -->` in its own source — so it is a fact
   *  read off the document, not the shell's idea of which chat is open. That distinction is the
   *  whole reason it is safe: a page is a meeting's page or it is not, and it says which in itself.
   *
   *  The server runs the meeting-doc variant of the act when it is here (`chat_intents.presets_for`):
   *  read the transcript since the page's own cursor, write into the page's regions, leave the
   *  widget slot alone. */
  meeting?: string;
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

/** EXTEND ON A PASSAGE OF A TRANSCRIPT (Vexa-ai/vexa#1596). Founder, 2026-09-06, in a live meeting
 *  with the canvas open: *"we also want extend on transcript when i can select some text and push
 *  the button"*.
 *
 *  A MEETING INTENT, AND THAT IS THE WHOLE DIFFERENCE. Extend on a page names the file it will edit;
 *  a passage of a transcript has no file behind it, so this names WHERE IN THE ROOM the words were
 *  said instead — and what the act writes are pages elsewhere, never the transcript, which stays a
 *  record of what was heard (the annotation layer of #1595 exists for the same reason).
 *
 *  Modelled on `ExploreIntent` and not on `PageIntentFields`: same family, same id vocabulary, and
 *  `isPageIntent` stays false — so no landing navigates the panel to a path nobody can predict. */
export interface ExtendTranscriptIntent {
  kind: "extend_transcript";
  /** the meeting ROW id the transcript belongs to — the id Highlight and a chip already send */
  meeting: string;
  /** the highlighted passage, trimmed and capped. Never absent: with nothing selected there is
   *  nothing to extend, and "the whole transcript" is not a selection. */
  selection: string;
  /** the segment the passage STARTS in, when it could be established exactly
   *  (`canvas/segmentSelection.ts` — the transcript's `sourceRange`). Provenance, never a join key. */
  segment?: string;
  /** who was speaking there, and when they said it (ISO 8601, UTC). Both are omitted rather than
   *  approximated when the passage cannot be located in exactly one segment. */
  speaker?: string;
  at?: string;
  /** the person's own line (#1593) — the same field, the same words, on the same control */
  instruction?: string;
}

/** THE POLICIES WIZARD (Vexa-ai/vexa#1627) — pressed on the policy page's own header.
 *
 *  A THIRD FAMILY, and the smallest one there is: it names a FILE like a page intent, and it is not
 *  one. `isPageIntent` stays false on purpose — a page intent LANDS (the panel navigates to the path
 *  when the turn commits), and this act opens a five-question conversation whose first turn writes
 *  nothing. Landing on its first commit would jump the reader away from the question they were
 *  being asked.
 *
 *  It carries no selection, no range and no instruction line. The wizard's first question is the
 *  field; a line typed on the button would be a sixth question asked before the five. */
export interface PoliciesWizardIntent {
  kind: "policies_wizard";
  /** the workspace the policy file lives in — `_global` for every deployment that has one */
  workspace?: string;
  /** the policy file the wizard walks and writes, from that workspace's root */
  path: string;
}

/** MEMBERSHIP IS A CONVERSATION (Vexa-ai/vexa#1632) — pressed on a workspace's own front page.
 *
 *  Founder, 2026-09-06, after *"Add a member…"* on a group page answered him with
 *  `invite role must be one of ('contributor',)`: *"this add member should just ask chat to do that
 *  with mcp, asking their emails etc."* and — on what the page should carry — *"so we do not have to
 *  create UI here — button to trigger the chat."*
 *
 *  A FOURTH FAMILY, and the only one that names neither a file nor a room. It names a WORKSPACE and,
 *  where the row had one, a PERSON. There is nothing else to name: the addresses, the role, and the
 *  yes are what the agent asks for, and asking is the whole act.
 *
 *  `isPageIntent` stays false, as it does for the wizard and for the same reason one issue on: a
 *  page intent LANDS when its turn commits, and there is no page to land on here — the act opens a
 *  conversation, and navigating away from the question would take the reader off the one screen the
 *  answer is wanted on.
 *
 *  IT CARRIES NO INSTRUCTION LINE AND NO SELECTION. The agent's own question is the field. A line
 *  typed on the button would be a second question asked before the first, and a form on the page is
 *  precisely what the founder's ruling removes — the confirmation lives in the chat, in one
 *  sentence, where a person can answer it in words.
 *
 *  `workspace` IS REQUIRED, and that is the F63 rule at its narrowest: these three acts change who
 *  can read and write somebody's workspace, so an act that does not name which workspace is not a
 *  near-miss to be repaired from whatever tab happened to be open — it is refused below.
 *
 *  SPLIT ONE INTERFACE PER KIND, like `extend`/`create` above and for the reason stated there — the
 *  three carry identical fields, so writing them as ONE record with `kind: "member_add" | …` reads
 *  like the tidier spelling. It is not: a member whose own `kind` is a union is not extractable by
 *  kind, so `IntentOf<"member_add">` collapses to `never`, and the compiler cannot rule the record
 *  out of the branch below either — `compactLabel`'s tail then reads `.path` off a type that has
 *  none. Both symptoms were observed on the way to this shape. The union is the validation. */
export interface MemberActFields {
  /** the workspace slug the act is about — REQUIRED, never guessed (F63) */
  workspace: string;
  /** the member the row named — their email when the roster has one, else the subject.
   *  Absent on `member_add`, which has nobody yet. */
  member?: string;
}

export interface MemberAddIntent extends MemberActFields { kind: "member_add" }
export interface MemberRoleIntent extends MemberActFields { kind: "member_role" }
export interface MemberRemoveIntent extends MemberActFields { kind: "member_remove" }
export type MemberActIntent = MemberAddIntent | MemberRoleIntent | MemberRemoveIntent;

/** WRITING A FLOW, AND SENDING WHAT WRITING ONE PRODUCED (Vexa-ai/vexa#1639).
 *
 *  Founder, 2026-09-06, in the governance chat of `_global`: *"we want to be able to write flows for
 *  the global chat as we like."*
 *
 *  It names a FILE, like the wizard one family up, and for the same reasons it is not a page intent:
 *  the act opens a conversation whose first turn writes nothing, and landing on the path when that
 *  turn commits would jump the reader away from the question they are being asked.
 *
 *  ONE KIND, TWO PAGES, and that is deliberate. Pressed on `flows/README.md` it means *write me a
 *  flow*; pressed on `flows/proposals/<slug>.md` — a step this image does not carry, written out for
 *  a developer — it means *send that to them*. The server's ask branches on the path, because the
 *  two are one conversation with one administrator about one thing, unlike the three membership acts
 *  whose labels a person must be able to tell apart.
 *
 *  It carries no selection and no instruction line: the agent's own question is the field. */
export interface FlowAuthorIntent {
  kind: "flow_author";
  /** the workspace the flows directory lives in — `_global` for every deployment that has one */
  workspace?: string;
  /** the page it was pressed on, from that workspace's root */
  path: string;
}

export type ChatIntent = ExtendIntent | CreateIntent | ExploreIntent | HighlightIntent | ExtendTranscriptIntent
  | PoliciesWizardIntent | MemberActIntent | FlowAuthorIntent;

/** The intents the person must NOT see as a bubble in their conversation. Mirrors
 *  `chat_intents.SILENT_KINDS` server-side — the founder's correction on Highlight is that it is
 *  silent, and a "Highlight: …" bubble would be the product narrating its own plumbing. */
export const SILENT_KINDS: ReadonlySet<ChatIntentKind> = new Set<ChatIntentKind>(["highlight"]);

export const isPageIntent = (i: ChatIntent): i is PageIntent => i.kind === "extend" || i.kind === "create";
export const isSilent = (i: ChatIntent): boolean => SILENT_KINDS.has(i.kind);

/** The three membership acts (Vexa-ai/vexa#1632) — as a set, and as the guard every consumer uses.
 *  `isPageIntent` one family along: the three kinds always travel together, so the places that
 *  handle them ask one question rather than three, and the set is the one place a fourth would be
 *  added. */
export const MEMBER_KINDS: ReadonlySet<ChatIntentKind> =
  new Set<ChatIntentKind>(["member_add", "member_role", "member_remove"]);
export const isMemberIntent = (i: ChatIntent): i is MemberActIntent => MEMBER_KINDS.has(i.kind);

/** The loose record a caller hands in — every field optional, because a button knows only its own. */
export type RawIntent = {
  kind: ChatIntentKind;
  workspace?: string | null;
  path?: string | null;
  selection?: string | null;
  selection_range?: { start: number; end: number } | null;
  instruction?: string | null;
  term?: string | null;
  meeting?: string | null;
  segment?: string | null;
  since?: string | null;
  speaker?: string | null;
  at?: string | null;
  member?: string | null;
};

const isInt = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;
const str = (v: unknown): string => String(v ?? "").trim();

/** ONE LINE, ALWAYS. A field one line high can still be PASTED into, and a newline reaching the act
 *  text would break the attributed block open — the person's words have to stay recognisably theirs
 *  and finite. Flattened, trimmed, capped; empty is ABSENT, never `""`.
 *
 *  One definition because BOTH families carry the line: a page act (#1593) and an act on a
 *  transcript passage (#1596) attribute the same words in the same way. */
const instructionOf = (raw: RawIntent): string | undefined => {
  const line = str(raw.instruction).replace(/\s+/g, " ").trim();
  return line ? line.slice(0, INSTRUCTION_MAX) : undefined;
};

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

  if (kind === "extend_transcript") {
    // NO MEETING, OR NOTHING HIGHLIGHTED, IS NOT AN ACT. The page form of Extend can fall back to
    // "the page as a whole"; this one cannot — a transcript's whole is the room, and an act on the
    // room with no words in it is exactly the guess this module exists to refuse.
    const meeting = str(raw.meeting);
    const selRaw = str(raw.selection);
    if (!meeting || !selRaw) return null;
    const selection = selRaw.slice(0, SELECTION_MAX);
    const segment = str(raw.segment);
    const speaker = str(raw.speaker);
    const at = str(raw.at);
    const instruction = instructionOf(raw);
    return {
      kind, meeting, selection,
      ...(segment ? { segment } : {}), ...(speaker ? { speaker } : {}), ...(at ? { at } : {}),
      ...(instruction ? { instruction } : {}),
    };
  }

  if (kind === "policies_wizard" || kind === "flow_author") {
    // THE SAME REFUSAL THE PAGE KINDS APPLY, and for the same reason: an act that names no file is
    // an act on a guess. There is no "the policy page as a whole" to fall back to, and the flow act
    // has to know WHICH page it was pressed on — the index and a proposal are two different halves
    // of the same conversation (Vexa-ai/vexa#1639).
    const path = String(raw.path ?? "").trim().replace(/^\/+/, "");
    if (!path || path.split("/").includes("..")) return null;
    const workspace = str(raw.workspace) || undefined;
    return { kind, ...(workspace ? { workspace } : {}), path };
  }

  if (kind === "member_add" || kind === "member_role" || kind === "member_remove") {
    // AN ACT ON NOBODY'S WORKSPACE IS NOT AN ACT (Vexa-ai/vexa#1632). These three change who reads
    // and writes a workspace, so the slug is the one thing that may never be inferred: refused
    // here, exactly as a page act with no path is, rather than repaired from the open tab.
    const workspace = str(raw.workspace);
    if (!workspace) return null;
    // ONE LINE, CAPPED. The roster hands this over — an email, or the subject when there is no
    // address — and it goes into a sentence the agent says back. A pasted paragraph arriving as a
    // "member" would be read aloud as a name, so it is bounded on the wire like every other
    // person-shaped field here. `member_add` has nobody yet, and absent is the honest answer.
    const member = str(raw.member).replace(/\s+/g, " ").trim().slice(0, MEMBER_MAX);
    return { kind, workspace, ...(member ? { member } : {}) };
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

  // ONE LINE, ALWAYS — see `instructionOf`, which both families share.
  const instruction = instructionOf(raw);

  // The page's own binding (#1598), carried only when the page declared one. Absent is the answer
  // for every page that is not a meeting's, which is nearly all of them.
  const meeting = str(raw.meeting) || undefined;

  return { kind, ...(workspace ? { workspace } : {}), path, ...(selection ? { selection } : {}), ...(selection_range ? { selection_range } : {}), ...(instruction ? { instruction } : {}), ...(meeting ? { meeting } : {}) };
}
