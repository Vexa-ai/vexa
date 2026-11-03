/** RIGHT → LEFT (UI 7h, 7q, 7i; feature `shell2`): chats about an artifact in the right panel.
 *
 *  "Chat about this" starts a chat in the centre with the artifact attached (its reference in the
 *  composer, the artifact kept open on the right); once a chat about it exists the button reads
 *  "Resume chat · n" and reopens the latest (reuse, never duplicate). Opening a chat FROM the panel
 *  changes only the centre: the panel keeps its view and scroll (`keepPanel`, read once by the shell).
 *
 *  No new backend: which chats are about which artifact is this browser's index (`vexa.about.v1`),
 *  filtered to chats the person still has (the shell publishes them), so a deleted chat drops out.
 *  The index is a convenience: losing it loses only the "Resume" count, never a chat. */
import { useSyncExternalStore } from "react";

export const CHAT_ABOUT_EVENT = "vexa:terminal:chat-about";
/** `ref` is the artifact's FULL reference (`[[meeting:<id>]]`, `[[page:<ws>/<path>]]`, `[[card:<id>]]`…):
 *  what "Chat about this" puts in the composer — never its title (founder 2026-10-18: many meetings
 *  are called "Call with Vexa"). */
export type Artifact = { key: string; slug?: string; path: string; label: string; kind: "page" | "meeting" | "card" | "person" | "room" | "workspace"; ref?: string };

/** The full reference of a page or a meeting, as the composer receives it. */
export function artifactRef(kind: string, slug: string | undefined, path: string): string {
  if (kind === "meeting") return `[[meeting:${path.replace(/^meeting:/, "")}]]`;
  return `[[page:${slug || "personal"}/${path.replace(/^\/+/, "")}]]`;
}
type Entry = { session: string; at: number };
const KEY = "vexa.about.v1";
const subs = new Set<() => void>();
let known: Set<string> | null = null;
const labels = new Map<string, string>();
let version = 0;
const emit = () => { version += 1; subs.forEach((f) => f()); };

function read(): Record<string, Entry[]> {
  try { const v = JSON.parse(localStorage.getItem(KEY) ?? "{}"); return v && typeof v === "object" ? v as Record<string, Entry[]> : {}; } catch { return {}; }
}
function write(v: Record<string, Entry[]>): void { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* storage off: no Resume count */ } }

/** What "Chat about this" puts in the composer: the artifact's full reference and nothing else — the
 *  composer draws it as a badge (name · date and time · owner), the agent receives the id. "" when the
 *  artifact has no reference (nothing is inserted rather than a bare title). */
export function chatAboutPrompt(a: Pick<Artifact, "kind" | "slug" | "path" | "ref">): string {
  const ref = a.ref || (a.kind === "meeting" || a.kind === "page" ? artifactRef(a.kind, a.slug, a.path) : "");
  return ref ? `${ref} ` : "";
}

/** The artifact key a panel path is known by. */
export function artifactOf(slug: string | undefined, path: string, kind: string | undefined, label: string): Artifact | null {
  if (!path || path.startsWith(".")) return null;
  if (kind === "meeting" || path.startsWith("meeting:")) return { key: `meeting:${path.replace(/^meeting:/, "")}`, slug, path, label, kind: "meeting", ref: artifactRef("meeting", slug, path) };
  return { key: `page:${slug || "personal"}/${path}`, slug, path, label, kind: "page", ref: artifactRef("page", slug, path) };
}

/** The shell says which chats exist (so a deleted one is not offered for Resume). */
export function setKnownChats(chats: { id: string; label?: string }[]): void {
  for (const c of chats) if (c.label) labels.set(c.id, c.label);
  const next = new Set(chats.map((c) => c.id));
  if (known && known.size === next.size && [...next].every((x) => known!.has(x))) return;
  known = next; emit();
}

export function rememberAbout(key: string, session: string): void {
  const all = read();
  all[key] = [...(all[key] ?? []).filter((e) => e.session !== session), { session, at: Date.now() }].slice(-20);
  write(all); emit();
}

/** Chats about this artifact, newest first, that still exist. */
export function aboutChats(key: string): Entry[] {
  return (read()[key] ?? []).filter((e) => !known || known.has(e.session)).sort((a, b) => b.at - a.at);
}

export function useAboutChats(key: string | null): Entry[] {
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => version, () => version);
  return key ? aboutChats(key) : [];
}

/** A chat's name as the rail shows it. */
export const chatLabel = (session: string): string => labels.get(session) || "Chat";

/** Read once by the shell when a chat opens: "leave the panel exactly as it is". */
let keepPanel = false;
let keepTimer: ReturnType<typeof setTimeout> | null = null;
export function holdPanel(): void {
  keepPanel = true;
  if (keepTimer) clearTimeout(keepTimer);
  keepTimer = setTimeout(() => { keepPanel = false; }, 4000);
}
export function consumeKeepPanel(): boolean { const k = keepPanel; keepPanel = false; return k; }
