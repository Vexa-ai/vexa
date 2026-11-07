/** PINNED PAGES ARE THE PERSON'S (#1783; founder: "pinned pages are in every session").
 *
 *  One ordered list per person, held by agent-api (`GET`/`PUT /api/user/pinned-pages`): pinning a
 *  page makes it a tab in every chat, new and existing, and unpinning removes it everywhere. A server
 *  that cannot answer leaves the last list this browser knew (local fallback). The agent's turn names
 *  the same list, by title and address. */
import { useSyncExternalStore } from "react";
import { artifactKey, orderHistory, type Artifact } from "./chats";

export type UserPin = { slug?: string | null; path: string; label: string };
const LOCAL_KEY = "vexa.minutes.userPins";

let pins: UserPin[] = readLocal();
/** Pages the person UNPINNED: a chat that kept its own copy of the pin drops it when next shown. */
const REMOVED_KEY = "vexa.minutes.userPinsRemoved";
let removed: Set<string> = (() => { try { return new Set(JSON.parse(localStorage.getItem(REMOVED_KEY) || "[]") as string[]); } catch { return new Set<string>(); } })();
const writeRemoved = () => { try { localStorage.setItem(REMOVED_KEY, JSON.stringify([...removed].slice(-200))); } catch { /* storage refused */ } };
let loaded = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

function readLocal(): UserPin[] {
  try {
    const v = JSON.parse(localStorage.getItem(LOCAL_KEY) || "[]") as unknown;
    return Array.isArray(v) ? v.filter((p): p is UserPin => !!p && typeof (p as UserPin).path === "string") : [];
  } catch { return []; }
}
function writeLocal(next: UserPin[]) { try { localStorage.setItem(LOCAL_KEY, JSON.stringify(next)); } catch { /* storage refused */ } }

export const pinKey = (p: { slug?: string | null; path: string }) => artifactKey({ path: p.path, slug: p.slug ?? undefined, label: "" } as Artifact);

export function useUserPins(): UserPin[] {
  return useSyncExternalStore((fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => pins, () => pins);
}
export function currentUserPins(): UserPin[] { return pins; }

/** Read the person's list from the server. `seed` is what to keep when the server has none yet and
 *  this browser never pinned anything (the pins the open chat already had). */
export async function loadUserPins(seed: UserPin[] = [], fetchImpl: typeof fetch = fetch): Promise<UserPin[]> {
  try {
    const r = await fetchImpl("/api/user/pinned-pages", { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const server = ((await r.json()) as { pages?: UserPin[] }).pages ?? [];
    loaded = true;
    if (server.length === 0 && pins.length === 0 && seed.length) { await saveUserPins(seed, fetchImpl); return pins; }
    if (server.length === 0 && pins.length > 0) { await saveUserPins(pins, fetchImpl); return pins; }
    pins = server; writeLocal(pins); emit();
  } catch { /* the last list this browser knew */ }
  return pins;
}

export async function saveUserPins(next: UserPin[], fetchImpl: typeof fetch = fetch): Promise<void> {
  pins = next; writeLocal(next); emit();
  try {
    await fetchImpl("/api/user/pinned-pages", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages: next.map((p) => ({ slug: p.slug || null, path: p.path, label: p.label || "" })) }),
    });
  } catch { /* kept locally; the next save carries it */ }
}

export function pinPage(p: UserPin): Promise<void> {
  if (removed.delete(pinKey(p))) writeRemoved();
  if (pins.some((x) => pinKey(x) === pinKey(p))) return Promise.resolve();
  return saveUserPins([...pins, p]);
}
export function unpinPage(p: { slug?: string | null; path: string }): Promise<void> {
  removed.add(pinKey(p)); writeRemoved();
  return saveUserPins(pins.filter((x) => pinKey(x) !== pinKey(p)));
}

/** A chat's tabs with the person's pins applied: every pin a tab (in the person's order), and a page
 *  the person unpinned (anywhere) is unpinned here too. A tab the chat pinned on its own (a meeting's
 *  pages) is left as it is. */
export function withUserPins<T extends Artifact>(list: T[], userPins: UserPin[], gone: Set<string> = removed): T[] {
  const keys = new Map(userPins.map((p, i) => [pinKey(p), i]));
  const have = new Set(list.map((a) => artifactKey(a)));
  const updated = list.map((a) => {
    if (a.desk || (a as { permanent?: boolean }).permanent) return a;
    const on = keys.has(artifactKey(a));
    if (on && !a.pinned) return { ...a, pinned: true };
    if (!on && a.pinned && gone.has(artifactKey(a))) return { ...a, pinned: undefined };
    return a;
  });
  const added = userPins.filter((p) => !have.has(pinKey(p)))
    .map((p) => ({ kind: "doc", path: p.path, slug: p.slug ?? undefined, label: p.label || p.path.split("/").pop() || p.path, pinned: true } as unknown as T));
  const all = orderHistory([...updated, ...added]) as T[];
  const rank = (a: T) => keys.get(artifactKey(a)) ?? 1e9;
  const desk = all.filter((a) => a.desk);
  const pinned = all.filter((a) => a.pinned && !a.desk).sort((x, y) => rank(x) - rank(y));
  const rest = all.filter((a) => !a.pinned && !a.desk);
  return [...desk, ...pinned, ...rest];
}

/** Tests only. */
export function resetUserPins(next: UserPin[] = [], isLoaded = false) { pins = next; loaded = isLoaded; removed = new Set(); emit(); }

/** A chat's panel and the person's pins (home2, founder 2026-10-16): a pin made in one chat no longer
 *  rides into every other chat's panel; the panel follows the chat in the centre. */
export function pinsForPanel<T extends Artifact>(list: T[], userPins: UserPin[], home2: boolean): T[] {
  return home2 ? list : withUserPins(list, userPins);
}
