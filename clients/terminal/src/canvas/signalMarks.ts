/** WHAT A TRANSCRIPT LINE SHOWS FROM ITS SIGNALS — the rules, pure (ADR transcript signals, part C).
 *
 *  A line gets a small CHIP for each WAKE label (the labels the meeting's brief says wake the agent,
 *  plus "addressed to Vexa", which always does) whose score is at or over the threshold: the brief's
 *  own `threshold` when it sets one, else DEFAULT_CHIP_THRESHOLD. Highlight-only labels (`wake:
 *  false`) never chip — they would turn the transcript into a wall of badges.
 *
 *  It also gets at most EMPHASIS.max words in a heavier weight: the most significant ones at or over
 *  EMPHASIS.min, never a short or filler word. Weight, not colour, so it reads in every theme and to
 *  someone who cannot tell colours apart; few, so the line still reads as speech. */
import type { AnnotationsBySegment, SignalAnnotation } from "../surfaces/meetingSignals";

export interface ListenLabel { name: string; question?: string; wake?: boolean }
export interface ListenBrief {
  focus?: string;
  labels: ListenLabel[];
  known?: string;
  wake_at?: number;
  max_wait_s?: number;
  version?: number;
  /** optional: the score a label needs to show as a chip on a line */
  threshold?: number;
}

/** The label the agent can never remove: a line said TO Vexa always wakes it. */
export const ADDRESSED = "addressed_to_vexa";
/** The wake labels a meeting has when it has no brief — agent-api's DEFAULT_BRIEF
 *  (`core/agent/control_plane/meeting_listen.py`, pinned by signalMarks.test). `blocker` is an
 *  example the agent may add to a brief, never a default. */
export const DEFAULT_WAKE_LABELS = ["decision", "action_item", ADDRESSED];
export const DEFAULT_CHIP_THRESHOLD = 0.6;
export const EMPHASIS = { min: 0.7, max: 3 } as const;

const NAMES: Record<string, string> = {
  decision: "Decision", action_item: "Action", action: "Action", blocker: "Blocker", [ADDRESSED]: "To Vexa",
};

/** A label as a person reads it: `action_item` → "Action", `budget_risk` → "Budget risk". */
export function labelText(name: string): string {
  const k = String(name ?? "").trim().toLowerCase();
  if (NAMES[k]) return NAMES[k];
  const words = k.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

/** The labels that wake the agent (and so chip a line): the brief's `wake: true` ones, plus
 *  addressed-to-Vexa; the standard four without a brief. */
export function wakeLabels(brief: ListenBrief | null | undefined): string[] {
  if (!brief || !Array.isArray(brief.labels) || !brief.labels.length) return [...DEFAULT_WAKE_LABELS];
  const out = brief.labels.filter((l) => l && l.wake === true && l.name).map((l) => l.name);
  if (!out.includes(ADDRESSED)) out.push(ADDRESSED);
  return out;
}

export interface SignalChip { name: string; label: string; score: number }

/** The chips for one annotation, strongest first. */
export function chipsFor(a: SignalAnnotation | undefined, brief?: ListenBrief | null): SignalChip[] {
  if (!a) return [];
  const t = typeof brief?.threshold === "number" && brief.threshold > 0 ? brief.threshold : DEFAULT_CHIP_THRESHOLD;
  return wakeLabels(brief)
    .map((name) => ({ name, label: labelText(name), score: a.scores?.[name] ?? 0 }))
    .filter((c) => c.score >= t)
    .sort((x, y) => y.score - x.score);
}

/** Words that never carry a line, whatever a model says about them. */
const FILLER = new Set(("the and but for with that this you are was were have has had not just like " +
  "yeah yes okay well so then them they their there what when which who will would could should " +
  "about into from your our its it's i'm we're").split(" "));

/** The words to emphasise on a line, lowercased, most significant first. */
export function emphasisWords(a: SignalAnnotation | undefined): string[] {
  if (!a?.words) return [];
  return Object.entries(a.words)
    .map(([w, n]) => [w.trim().toLowerCase(), n] as const)
    .filter(([w, n]) => w.length >= 3 && !FILLER.has(w) && n >= EMPHASIS.min && /\p{L}/u.test(w))
    .sort((x, y) => y[1] - x[1])
    .slice(0, EMPHASIS.max)
    .map(([w]) => w);
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` cut into pieces, whole words in `words` marked strong (case-insensitive). */
export function splitEmphasis(text: string, words: string[]): { text: string; strong: boolean }[] {
  if (!words.length || !text) return [{ text, strong: false }];
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${words.map(escape).join("|")})(?![\\p{L}\\p{N}])`, "giu");
  const out: { text: string; strong: boolean }[] = [];
  let at = 0;
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > at) out.push({ text: text.slice(at, i), strong: false });
    out.push({ text: m[0], strong: true });
    at = i + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at), strong: false });
  return out.length ? out : [{ text, strong: false }];
}

export interface SegmentMarks { chips: SignalChip[]; words: string[] }
/** What the renderer asks per line: its marks, or undefined for a line with nothing to show. */
export type MarksFor = (segmentId?: string) => SegmentMarks | undefined;

export function marksFor(annotations: AnnotationsBySegment, brief?: ListenBrief | null): MarksFor {
  return (segmentId) => {
    if (!segmentId) return undefined;
    const a = annotations[segmentId];
    if (!a) return undefined;
    const chips = chipsFor(a, brief), words = emphasisWords(a);
    return chips.length || words.length ? { chips, words } : undefined;
  };
}
