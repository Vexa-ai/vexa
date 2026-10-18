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
export type Artifact = { key: string; slug?: string; path: string; label: string; kind: "page" | "meeting" };
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

/** The artifact key a panel path is known by. */
export function artifactOf(slug: string | undefined, path: string, kind: string | undefined, label: string): Artifact | null {
  if (!path || path.startsWith(".")) return null;
  if (kind === "meeting" || path.startsWith("meeting:")) return { key: `meeting:${path.replace(/^meeting:/, "")}`, slug, path, label, kind: "meeting" };
  return { key: `page:${slug || "personal"}/${path}`, slug, path, label, kind: "page" };
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
