/** THE LISTENING BRIEF on the meeting page (ADR transcript signals): the `listen:` block of the page's
 *  front matter — what the signal model listens for in this meeting, which labels wake the agent, and
 *  how sure it must be. The meeting's agent writes it (`meeting_listen`); the people in the meeting
 *  can read it on the page and change it through the page. This module reads that block and writes
 *  an edit back into it, leaving every other key and the page body exactly as they were.
 *
 *  Edits keep two rules the agent keeps: "addressed to Vexa" is always there and always wakes, and
 *  every save bumps `version`, so the signals service can tell which brief a score was made under. */
import { isMap, parseDocument } from "yaml";
import { ADDRESSED, type ListenBrief, type ListenLabel } from "./signalMarks";

function frontMatter(raw: string): { fm: string; start: number; end: number } | null {
  if (!raw.startsWith("---")) return null;
  const close = raw.indexOf("\n---", 3);
  if (close === -1) return null;
  const firstNl = raw.indexOf("\n");
  return { fm: raw.slice(firstNl + 1, close + 1), start: firstNl + 1, end: close + 1 };
}

const str = (v: unknown) => (typeof v === "string" ? v : undefined);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** A label name as the brief stores it: lowercase words joined by `_`. */
export function labelName(name: string): string {
  return String(name ?? "").trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "_").replace(/^_+|_+$/g, "").slice(0, 48);
}

function toBrief(v: unknown): ListenBrief | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const labels: ListenLabel[] = (Array.isArray(o.labels) ? o.labels : [])
    .map((l) => (typeof l === "string" ? { name: l } : l && typeof l === "object" ? (l as Record<string, unknown>) : null))
    .filter((l): l is Record<string, unknown> => !!l && typeof l.name === "string" && !!(l.name as string).trim())
    .map((l) => ({ name: String(l.name), ...(str(l.question) ? { question: str(l.question) } : {}),
      ...(typeof l.wake === "boolean" ? { wake: l.wake } : {}) }));
  return {
    labels,
    ...(str(o.focus) !== undefined ? { focus: str(o.focus) } : {}),
    ...(str(o.known) !== undefined ? { known: str(o.known) } : {}),
    ...(num(o.wake_at) !== undefined ? { wake_at: num(o.wake_at) } : {}),
    ...(num(o.max_wait_s) !== undefined ? { max_wait_s: num(o.max_wait_s) } : {}),
    ...(num(o.version) !== undefined ? { version: num(o.version) } : {}),
    ...(num(o.threshold) !== undefined ? { threshold: num(o.threshold) } : {}),
  };
}

/** The page's brief, or null when the page has none (or its front matter does not parse). */
export function readListenBrief(raw: string): ListenBrief | null {
  const f = frontMatter(raw ?? "");
  if (!f) return null;
  try {
    const doc = parseDocument(f.fm);
    if (doc.errors.length) return null;
    return toBrief((doc.toJS() as Record<string, unknown> | null)?.listen);
  } catch { return null; }
}

/** The page with `brief` written into its `listen:` block — names normalised, empty labels dropped,
 *  addressed-to-Vexa restored and waking, `version` one past the page's current one. The rest of the
 *  front matter (its comments too) and the body are left as they were. */
export function writeListenBrief(raw: string, brief: ListenBrief): string {
  const f = frontMatter(raw);
  const prev = readListenBrief(raw);
  const seen = new Set<string>();
  const labels = (brief.labels ?? [])
    .map((l) => ({ ...l, name: labelName(l.name) }))
    .filter((l) => l.name && !seen.has(l.name) && seen.add(l.name))
    .map((l) => ({ name: l.name, ...(l.question?.trim() ? { question: l.question.trim() } : {}), wake: l.name === ADDRESSED ? true : !!l.wake }));
  if (!labels.some((l) => l.name === ADDRESSED)) labels.push({ name: ADDRESSED, wake: true });
  const next: Record<string, unknown> = {
    focus: (brief.focus ?? "").trim(),
    labels,
    ...(brief.known !== undefined ? { known: brief.known } : prev?.known !== undefined ? { known: prev.known } : {}),
    ...(brief.wake_at !== undefined ? { wake_at: brief.wake_at } : prev?.wake_at !== undefined ? { wake_at: prev.wake_at } : {}),
    ...(brief.max_wait_s !== undefined ? { max_wait_s: brief.max_wait_s } : prev?.max_wait_s !== undefined ? { max_wait_s: prev.max_wait_s } : {}),
    ...(brief.threshold !== undefined ? { threshold: brief.threshold } : prev?.threshold !== undefined ? { threshold: prev.threshold } : {}),
    version: Math.max(prev?.version ?? 0, brief.version ?? 0) + 1,
  };
  const doc = parseDocument(f ? f.fm : "");
  if (!isMap(doc.contents)) doc.contents = doc.createNode({}) as unknown as typeof doc.contents;
  doc.set("listen", doc.createNode(next));
  const fm = doc.toString({ lineWidth: 0 });
  if (!f) return `---\n${fm}---\n\n${raw}`;
  return raw.slice(0, f.start) + fm + raw.slice(f.end);
}
