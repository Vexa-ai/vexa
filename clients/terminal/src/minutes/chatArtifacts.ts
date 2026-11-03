/** THE RIGHT PANEL BELONGS TO THE AGENT ON ITS LEFT (founder, 2026-10-15: *"right sidebar
 *  artefacts belong to the agent on the left side of it"*).
 *
 *  What the panel lists when nothing is open is this chat's own artifacts — pages the agent wrote,
 *  pages opened in this chat, cards it updated, the meeting it is about — newest first, one line
 *  each. Never a workspace tree (that is "All files", one click deeper) and never anything from
 *  another chat. Pure, so the list is decided here and tested here; the shell only feeds it. */

export type ChatArtifactKind = "wrote" | "opened" | "card" | "meeting";
export type ChatArtifact = {
  key: string;
  kind: ChatArtifactKind;
  title: string;
  at?: number;
  /** what a click opens: a file path (as the chat's chips carry it), a page, a card or a meeting */
  file?: string;
  page?: { kind?: "doc" | "meeting"; path: string; slug?: string; label: string };
  card?: string;
  meeting?: string;
};

type Visit = { kind?: "doc" | "meeting"; path: string; slug?: string; label: string; at?: number; desk?: boolean; home?: boolean };
type Turn = { role: string; at?: number; ops?: { file?: string; wrote?: boolean }[] };

/** The files this chat's turns wrote, with the turn's time. Read off the turns the chat already has. */
export function writesOf(turns: Turn[]): { file: string; at?: number }[] {
  const out: { file: string; at?: number }[] = [];
  for (const t of turns) {
    if (t.role !== "agent") continue;
    for (const o of t.ops ?? []) if (o.wrote && o.file) out.push({ file: o.file, ...(typeof t.at === "number" ? { at: t.at } : {}) });
  }
  return out;
}

/** "sponsor-plan.md" → "Sponsor plan": a page is named, not addressed. */
export function pageTitleOf(path: string): string {
  const leaf = path.split("/").filter(Boolean).pop() ?? path;
  const bare = leaf.replace(/\.(md|mdx|markdown|txt)$/i, "").replace(/[-_]+/g, " ").trim();
  return bare ? bare.charAt(0).toUpperCase() + bare.slice(1) : leaf;
}

/** Two references to the same file match on their tail — a write carries the agent's absolute
 *  path, a visit the workspace-relative one. */
const sameFile = (abs: string, rel: string) => {
  const a = abs.replace(/^\/+/, ""), r = rel.replace(/^\/+/, "");
  return a === r || a.endsWith(`/${r}`);
};

export function chatArtifacts(src: {
  writes: { file: string; at?: number }[];
  visits: Visit[];
  cards: { id: string; title: string; at?: number; page?: string }[];
  meeting?: { id: string; title: string; at?: number };
}): ChatArtifact[] {
  const items: ChatArtifact[] = [];
  const latest = new Map<string, { file: string; at?: number }>();
  for (const w of src.writes) {
    const prev = latest.get(w.file);
    if (!prev || (w.at ?? 0) >= (prev.at ?? 0)) latest.set(w.file, w);
  }
  const visits = src.visits.filter((v) => !v.desk && !v.home && v.kind !== "meeting");
  for (const w of latest.values()) {
    // a page the chat wrote and then opened is ONE artifact, at the later of the two
    const seen = visits.find((v) => sameFile(w.file, v.path));
    const at = Math.max(w.at ?? 0, seen?.at ?? 0) || undefined;
    items.push({ key: `w:${w.file}`, kind: "wrote", title: pageTitleOf(w.file), at, file: w.file,
      ...(seen ? { page: { kind: seen.kind, path: seen.path, slug: seen.slug, label: seen.label } } : {}) });
  }
  for (const v of visits) {
    if ([...latest.keys()].some((f) => sameFile(f, v.path))) continue;
    items.push({ key: `o:${v.slug ?? ""}|${v.path}`, kind: "opened", title: pageTitleOf(v.path), at: v.at,
      page: { kind: v.kind, path: v.path, slug: v.slug, label: v.label } });
  }
  for (const c of src.cards) items.push({ key: `c:${c.id}`, kind: "card", title: c.title, at: c.at, card: c.id, ...(c.page ? { file: c.page } : {}) });
  if (src.meeting) items.push({ key: `m:${src.meeting.id}`, kind: "meeting", title: src.meeting.title, at: src.meeting.at, meeting: src.meeting.id });
  return items.sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
}

const VERB: Record<ChatArtifactKind, string> = { wrote: "Wrote", opened: "Opened", card: "Updated card", meeting: "Meeting with" };
export const artifactVerb = (k: ChatArtifactKind): string => VERB[k];

export function artifactTime(at?: number): string {
  if (!at) return "";
  const d = new Date(at);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** "Wrote Sponsor plan · 10:12" */
export function artifactLine(a: ChatArtifact): string {
  const t = artifactTime(a.at);
  return `${VERB[a.kind]} ${a.title}${t ? ` · ${t}` : ""}`;
}
