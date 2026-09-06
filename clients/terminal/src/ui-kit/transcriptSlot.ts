/** THE TRANSCRIPT SLOT — how a meeting's own page says "the live transcript goes HERE".
 *
 *  Founder, 2026-09-06, in a live meeting (Vexa-ai/vexa#1598): *"this just means doc reading
 *  transcript which we have and we want this doc to open alongside transcript as a single thing in
 *  the right side so it's a kind of doc that has live transcript widget in it"*. So a meeting is ONE
 *  page on the right — the meeting doc — and the transcript is a widget INSIDE it, not a second tab.
 *
 *  THE MARKER IS AN HTML COMMENT, and that is the whole design decision:
 *
 *      <!-- vexa:transcript meeting=147 -->
 *
 *  The file stays a plain markdown file for every other reader — GitHub, Obsidian, `cat`, the mail
 *  that carries the report, the next agent that reads it as text — because an HTML comment renders
 *  as nothing everywhere. An MDX tag (`<Transcript meeting="147" />`) would have been the shorter
 *  route and it fails exactly where it matters: the same file is written by a flow, read by an
 *  agent, mailed to attendees and opened in three renderers, and only one of them speaks MDX. A
 *  marker every other reader IGNORES beats one they all have to learn.
 *
 *  It also means this renderer must split BEFORE comments are stripped (#1590 drops HTML comments so
 *  the desk README's region markers do not render as prose) — a slot dropped as machinery would take
 *  the live transcript with it. `MdxDoc` does the split first for that reason.
 *
 *  ONE SPELLING, TWO LANGUAGES. `core/agent/shared/meeting_doc.py` writes this marker; this file
 *  reads it. They are pinned together by `gate:fact-parity` (`transcript-slot-marker` in
 *  `scripts/parity.json`) rather than by a comment asking two files to be nice to each other.
 */

/** The tab kind the widget registers under (`contributions`), so ui-kit never imports the canvas. */
export const TRANSCRIPT_WIDGET_KIND = "transcript-widget";

/** The marker, as the server writes it and as this reads it. One function, so a caller that needs
 *  to EMIT one cannot invent a second spelling. */
export function transcriptSlotMarker(meeting: string): string {
  return `<!-- vexa:transcript meeting=${String(meeting ?? "").trim()} -->`;
}

/** The marker's source, as a string, so the parity gate can compare it against the Python copy
 *  character for character. Built into a fresh RegExp at every use — a module-level `g` regex
 *  carries `lastIndex` between calls, which is the classic way a second render of the same doc
 *  silently loses its widget. */
export const TRANSCRIPT_SLOT_SOURCE = "<!--\\s*vexa:transcript\\s+meeting=[\"']?([A-Za-z0-9_.:-]{1,128})[\"']?\\s*-->";

const slotRe = (flags = "") => new RegExp(TRANSCRIPT_SLOT_SOURCE, flags);

export type DocSegment =
  | { kind: "text"; text: string }
  | { kind: "transcript"; meeting: string };

/** Does this document declare a transcript widget? (Fences excluded — see `splitTranscriptSlots`.) */
export function hasTranscriptSlot(src: string): boolean {
  return splitTranscriptSlots(src).some((s) => s.kind === "transcript");
}

/** WHICH MEETING THIS PAGE IS, according to the page — or `""`.
 *
 *  The page's own binding, and the reason the Extend act on a meeting doc can be the meeting-doc
 *  variant without the shell being asked which chat is open: a document either declares a room or it
 *  does not, and it declares it in itself. A shell-derived answer would follow the reader's tabs; a
 *  document-derived one follows the document, which is what the act is about. */
export function transcriptSlotMeeting(src: string): string {
  const hit = splitTranscriptSlots(src).find((s) => s.kind === "transcript");
  return hit && hit.kind === "transcript" ? hit.meeting : "";
}

/** A document → the parts it renders as, in order.
 *
 *  A document with no marker returns EXACTLY ONE text segment holding the source unchanged, so the
 *  ordinary page — every page but a meeting's — takes the identical path it took before this
 *  existed. Nothing about a normal doc's rendering moves.
 *
 *  FENCES ARE LITERAL, the same rule `transformDocRefs` keeps one file away: a fenced block is a
 *  transcript of text somebody means to copy, and a doc that DOCUMENTS this marker (this file's own
 *  README, an ask preset showing the shape) must show it, not sprout a live meeting inside a code
 *  sample. Inline code counts too, for the same reason.
 *
 *  Empty text between two markers is dropped rather than rendered as a blank MDX body. */
export function splitTranscriptSlots(src: string): DocSegment[] {
  const text = String(src ?? "");
  if (!slotRe().test(text)) return [{ kind: "text", text }];
  const out: DocSegment[] = [];
  const push = (t: string) => { if (t.trim()) out.push({ kind: "text", text: t }); };
  // odd indices are a fenced block or an inline-code run — passed through untouched
  text.split(/(```[\s\S]*?```|`[^`\n]*`)/g).forEach((chunk, i) => {
    if (i % 2 === 1) { push(chunk); return; }
    let at = 0;
    const re = slotRe("g");
    let m: RegExpExecArray | null;
    while ((m = re.exec(chunk)) != null) {
      push(chunk.slice(at, m.index));
      out.push({ kind: "transcript", meeting: m[1] });
      at = re.lastIndex;
    }
    push(chunk.slice(at));
  });
  // A doc that is nothing but a marker still has to render something; the widget IS that something,
  // so an empty list can only mean the split lost the source — return it rather than a blank page.
  return out.length ? out : [{ kind: "text", text }];
}
