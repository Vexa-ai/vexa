/** "EXTEND" — send the open chat to explore the page, or a piece of it (PRD decision 32).
 *
 *  Founder: *"an extend button that would request the same chat that is open now — just go explore
 *  the page in question or a highlighted text from a file, so the agent would go explore that
 *  further following the logic of the context and the chat and the focus."*
 *
 *  THE SAME CHAT, NOT A NEW ONE. It posts a turn into whatever chat is open, through the same
 *  `ASK_CHAT_EVENT` seam a canvas chip already uses — so the turn inherits that conversation's
 *  context, focus and mount set by construction rather than by re-composing them here.
 *
 *  WHAT THE PERSON SEES IS THE COMPACT FORM. `Extend: kg/plan.md — "…"`, never the prompt. The
 *  preset text is the agent's business; a bubble that renders it puts words in the person's mouth
 *  they did not write, and then their next message argues with a paragraph they never said. The ask
 *  seam has carried a separate `display` since the canvas chips — this is the same rule, and when
 *  F47's `user_text` lands on the record it is the same rule again, one layer down.
 *
 *  UNTIL THE SERVER HALF LANDS, BOTH TRAVEL. The typed `intent` is what the `extend` preset will
 *  read; the prompt is the plain sentence that works today. A build where the server ignores the
 *  intent still does the right thing, and a build where it reads it is not confused by the
 *  sentence — the two say the same thing.
 */
import { ASK_CHAT_EVENT } from "../canvas/actions";
import { isPageIntent, isSilent, normalizeIntent, type ChatIntent, type ChatIntentKind, type ExtendTranscriptIntent, type IntentOf, type RawIntent } from "../surfaces/chatIntent";
import { navigateView } from "./roomView";

/** How much of a selection the BUBBLE shows. The intent carries up to 2000 characters; a bubble is
 *  a label, and a paragraph rendered as one is the composed-text failure wearing a quotation mark. */
export const PREVIEW_MAX = 80;

const VERB: Record<ChatIntentKind, string> = {
  extend: "Extend", create: "Create", explore: "Explore", highlight: "Highlight",
  // Extend on a transcript passage is EXTEND to the person who pressed it — the same word on the
  // same control (Vexa-ai/vexa#1596). Only the kind differs, because only the server needs to know
  // that this one names a room rather than a file. `shared/marks._ACT_VERBS` says the same thing on
  // the server side, for the label a reload rebuilds from the record.
  extend_transcript: "Extend",
};

/** HOW THE PERSON'S OWN LINE IS INTRODUCED to the agent (Vexa-ai/vexa#1593). One sentence, and the
 *  same one the server writes when a preset carries no `{{instruction}}` token
 *  (`chat_intents.INSTRUCTION_LEAD`) and the same one the two asks put above it — three spellings
 *  of one thing would be three ways for the agent to read it differently. It says WHOSE words
 *  follow, because that is the whole point: the preset is ours, this line is theirs. */
export const INSTRUCTION_LEAD = "They typed this on the button, in their own words — what to do with it:";

/** Collapse the whitespace a rendered selection carries — a highlight dragged across a paragraph
 *  break arrives with newlines in it, and they belong in the intent, never in a one-line label. */
const oneLine = (s: string) => s.replace(/\s+/g, " ").trim();

/** The quotation a label carries: one line, and short enough to stay a label. */
function preview(selection: string): string {
  const flat = oneLine(selection);
  return flat.length > PREVIEW_MAX ? `${flat.slice(0, PREVIEW_MAX).trimEnd()}…` : flat;
}

/** THE BUBBLE. Compact by construction: a verb, the page, and — when there is one — a short
 *  quotation of what was highlighted. */
export function compactLabel(intent: ChatIntent): string {
  // A CHIP CLICKED IN A TRANSCRIPT SHOWS THE WORDS, not the meeting it was said in: the person is
  // looking at the room already, and "Explore: Kaar Tech (meeting 41, segment …)" spends the whole
  // label on the two facts they can see.
  if (intent.kind === "explore") return `Explore: ${intent.term}`;
  // Highlight is silent (decision 35.2) and never reaches a bubble; the label exists only so a
  // caller that logs one has something honest to log.
  if (intent.kind === "highlight") return "Highlight";
  // A TRANSCRIPT PASSAGE HAS NO PAGE TO NAME (Vexa-ai/vexa#1596), so the label names the room and
  // quotes the words — the same two facts, in the same order and with the same separator, that the
  // server writes into the job mark (`chat_intents.job_target`). The bubble the person watches and
  // the label a reload rebuilds from the record therefore read alike.
  if (intent.kind === "extend_transcript") {
    return `${VERB[intent.kind]}: meeting ${intent.meeting} · “${preview(intent.selection)}”`;
  }
  const head = `${VERB[intent.kind]}: ${intent.path}`;
  if (!intent.selection) return head;
  return `${head} — “${preview(intent.selection)}”`;
}

/** THE PROMPT, until the server turns the intent into the `extend` preset. The whole selection,
 *  not the preview: this is what the agent reads, and truncating it here would lose the half of a
 *  paragraph the person actually cared about. */
export function fallbackText(intent: ChatIntent): string {
  if (intent.kind === "explore") {
    return `Explore \`${intent.term}\` (said in meeting ${intent.meeting}` +
      (intent.segment ? `, segment ${intent.segment}` : "") +
      `): find out what it is in the logic of this chat and this meeting — workspace first, ` +
      `research where it runs out — write its page with sources, then two lines on what it is.`;
  }
  if (intent.kind === "highlight") {
    return `Call transcript_terms(meeting_id="${intent.meeting}", since="${intent.since ?? ""}"), ` +
      `pick the terms that matter to this person in this meeting, then call it again with ` +
      `keep="<those terms>" to publish them as chips. Say nothing back — this is machinery.`;
  }
  // The two page kinds name a FILE and the transcript one names a ROOM (Vexa-ai/vexa#1596); past
  // that they are the same act, and the person's own line rides all three identically — so it is
  // appended once, below, rather than in each branch.
  const where = intent.kind === "extend_transcript" ? transcriptFallback(intent)
    : `${VERB[intent.kind]}: ${intent.path}` + (intent.selection ? ` — '${intent.selection}'` : "");
  // THE LINE RIDES THE FALLBACK TOO. This sentence is what runs when the preset library is behind
  // the client (the header says why both travel), and a fallback that dropped the one thing the
  // person typed would be the worst of the two failures: the act still runs, on the wrong subject,
  // with nothing to say it ignored them.
  return intent.instruction ? `${where}\n\n${INSTRUCTION_LEAD}\n\n${intent.instruction}` : where;
}

/** The plain sentence for an act on a transcript passage — what runs when this deployment's preset
 *  library has no `extend-transcript.md` yet. It says the same things that ask says: where the words
 *  were said, that the pages are the deliverable, that the terms go back onto the transcript, and
 *  that the transcript itself is never rewritten. */
function transcriptFallback(intent: ExtendTranscriptIntent): string {
  const said = [intent.speaker ? `said by ${intent.speaker}` : "", intent.at ? `at ${intent.at}` : "",
    intent.segment ? `segment ${intent.segment}` : ""].filter(Boolean).join(", ");
  return `Extend on what was said in meeting ${intent.meeting}${said ? ` (${said})` : ""}: ` +
    `'${intent.selection}' — research it in the logic of this chat and this meeting, write what ` +
    `you find as pages with their sources and link both ways, then publish the terms it named ` +
    `onto the transcript with transcript_terms(meeting_id="${intent.meeting}", keep="<those terms>"). ` +
    `Never rewrite the transcript. Then say ONE line about what you wrote.`;
}

/** WHERE A SELECTION SITS IN THE FILE SOURCE — or nothing.
 *
 *  The rendered document is not the file: a heading loses its `#`, a link loses its target, and an
 *  offset into what the reader highlighted is an offset into neither. So the range is established
 *  by finding the selection in the SOURCE, and only when it occurs there exactly ONCE. Two
 *  occurrences is not a near-miss, it is an unknown answer (F63), and an unknown answer is omitted.
 */
export function sourceRange(body: string | null | undefined, selection: string): { start: number; end: number } | null {
  const src = body ?? "";
  const needle = selection.trim();
  if (!src || !needle) return null;
  const first = src.indexOf(needle);
  if (first < 0) return null;
  if (src.indexOf(needle, first + 1) >= 0) return null;   // ambiguous → no range
  return { start: first, end: first + needle.length };
}

// ── posting, and where the reply lands ───────────────────────────────────────────────────────────

/** The page an in-flight intent will bring into view once the turn commits. Module-level because
 *  the button that posts and the listener that lands are two different components with one fact
 *  between them — and it is a POINTER, not state: one intent is pending at a time, the newest wins,
 *  which is exactly what a second press of Extend means. */
let pending: { workspace?: string; path: string } | null = null;

/** For tests and for a panel that unmounts mid-turn. */
export const pendingLanding = (): { workspace?: string; path: string } | null => pending;
export const clearPending = (): void => { pending = null; };

/** Post an intent into the OPEN chat. Returns the intent that went, or `null` when there was
 *  nothing honest to send (see `normalizeIntent` — an unnamed page is never guessed at). */
export function postIntent<K extends ChatIntentKind>(raw: Omit<RawIntent, "kind"> & { kind: K }): IntentOf<K> | null;
export function postIntent(raw: RawIntent): ChatIntent | null;
export function postIntent(raw: RawIntent): ChatIntent | null {
  const intent = normalizeIntent(raw);
  if (!intent) return null;
  // ONLY A PAGE INTENT HAS A LANDING. `explore` writes a page whose path nobody can predict — the
  // agent picks the kind and the slug — so navigating on its commit would land the panel on a
  // guess. Its visible result is the chip going solid, which the terms layer does on the same
  // commit event. `highlight` writes nothing at all, and `extend_transcript` may write SEVERAL
  // pages (Vexa-ai/vexa#1596): landing on one of them would pick a winner nobody chose.
  pending = isPageIntent(intent) ? { workspace: intent.workspace, path: intent.path } : null;
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
    detail: {
      prompt: fallbackText(intent),
      display: compactLabel(intent),
      intent,
      // `hidden` suppresses the user bubble for a machinery turn. The chat's own MACHINERY_MARK
      // keeps it hidden on RELOAD too, which is the half `hidden` alone has never covered.
      ...(isSilent(intent) ? { hidden: true } : {}),
    },
  }));
  return intent;
}

/** THE LANDING (decision 32.3): after the reply, the page the intent named becomes the view.
 *
 *  It fires on the turn's COMMIT, not on the last token — for `create` the file does not exist
 *  until then, and navigating to it a moment early is how the panel ends up saying "no page here
 *  yet" about a page that was just written. A turn that commits nothing lands nothing, which is
 *  the honest answer: the page did not change.
 *
 *  It NAVIGATES, it does not open a tab (decision 28). */
export function landPending(): boolean {
  if (!pending) return false;
  const { workspace, path } = pending;
  pending = null;
  navigateView(workspace, path);
  return true;
}
