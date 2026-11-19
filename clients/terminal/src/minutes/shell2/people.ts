/** THE PEOPLE YOU CAN SEE (founder 2026-10-17, Slack-like; feature `shell2`).
 *
 *  - **In my workspaces** (the default): every human who shares at least one workspace with you —
 *    the union of your workspaces' rosters, `GET /api/people/peers` (agent-api, over the membership
 *    rosters; refused to any shared/room token), each once, with their role per shared workspace.
 *  - **Contacts**: people you share no workspace with, only as the CRM lets you see them — the
 *    interactions set `GET /api/people/known` (merged by account and address; S3 makes each human
 *    one Person there). Shown only when the CRM is on.
 *  - **Recent**: anyone you interacted with in the last 14 days.
 *
 *  Each human appears once: rows merge by account, then by address. Presence comes from the
 *  desktop's member lists (online now / active today). Pure merge + a small cached read. */
import { useEffect, useSyncExternalStore } from "react";
import type { KnownPerson } from "../../surfaces/sessionsApi";
import { listKnownPeople } from "../../surfaces/sessionsApi";
import { isMe } from "./me";

export type Presence = "now" | "today" | null;
export type Peer = { subject: string; name: string; email?: string; workspaces: string[]; roles?: Record<string, string> };
export type Person = {
  key: string; subject: string | null; name: string; email: string;
  /** the workspaces you share, with their role in each */
  workspaces: string[]; roles: Record<string, string>;
  vexa: boolean; how: string[]; last: number | null; page: string | null;
  photo?: string; avatar?: string; logo?: string; headline?: string; presence: Presence;
  /** S3: how many logins this one human has */
  accounts?: number;
  /** the other keys this human is known by (the interactions set's key), so any opener finds them */
  aliases?: string[];
};

const PERSONAL_DOMAINS = new Set(["gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "live.com", "aol.com"]);
/** A company word from an address ("ana@brightpine.example" → "Brightpine"); none for a mailbox provider. */
export function companyOf(email: string): string {
  const d = email.split("@")[1]?.toLowerCase() ?? "";
  if (!d || PERSONAL_DOMAINS.has(d)) return "";
  const label = d.split(".")[0] ?? "";
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : "";
}

export const RECENT_DAYS = 14;
export const isRecent = (p: Person, now = Date.now()): boolean => !!p.last && now - p.last * (p.last < 1e12 ? 1000 : 1) < RECENT_DAYS * 86_400_000;

/** One row per human: peers first (the roster), then whoever else the interactions set names. */
export function mergePeople(peers: readonly Peer[], known: readonly KnownPerson[], presence: (subject: string) => Presence = () => null): Person[] {
  const out: Person[] = [];
  const bySubject = new Map<string, Person>();
  const byEmail = new Map<string, Person>();
  const add = (p: Person) => { out.push(p); if (p.subject) bySubject.set(p.subject, p); if (p.email) byEmail.set(p.email.toLowerCase(), p); };
  for (const r of peers) {
    if (!r.subject || isMe({ subject: r.subject, email: r.email, name: r.name })) continue;
    if (bySubject.has(r.subject)) continue;
    add({ key: `u:${r.subject}`, subject: r.subject, name: r.name || (r.email ?? "").split("@")[0] || "Someone", email: r.email ?? "",
      workspaces: [...r.workspaces], roles: { ...(r.roles ?? {}) }, vexa: true, how: ["workspace"], last: null, page: null, presence: presence(r.subject) });
  }
  for (const k of known) {
    const email = (k.person.email ?? "").toLowerCase();
    if (isMe({ subject: k.subject, email, name: k.name })) continue;
    const hit = (k.subject && bySubject.get(k.subject)) || (email && byEmail.get(email));
    if (hit) {
      hit.aliases = [...(hit.aliases ?? []), k.key];
      hit.how = [...new Set([...hit.how, ...k.how])]; hit.last = k.last ?? hit.last; hit.page = k.page ?? hit.page;
      hit.photo = k.person.photo ?? hit.photo; hit.avatar = k.person.avatar ?? hit.avatar; hit.logo = k.person.logo ?? hit.logo;
      hit.headline = k.person.role ?? hit.headline;
      const acc = (k as { accounts?: number }).accounts; if (acc) hit.accounts = acc;
      if (!hit.email && email) { hit.email = email; byEmail.set(email, hit); }
      continue;
    }
    add({ key: k.key, subject: k.subject, name: k.name, email, workspaces: [], roles: {}, vexa: k.vexa, how: [...k.how], last: k.last,
      page: k.page, photo: k.person.photo, avatar: k.person.avatar, logo: k.person.logo, headline: k.person.role,
      presence: k.subject ? presence(k.subject) : null, accounts: (k as { accounts?: number }).accounts });
  }
  return out;
}

export type Scope = "workspaces" | "recent" | "contacts" | "all";
export function inScope(p: Person, s: Scope, now = Date.now()): boolean {
  if (s === "workspaces") return p.workspaces.length > 0;
  if (s === "recent") return isRecent(p, now);
  if (s === "contacts") return p.workspaces.length === 0;
  return true;
}
export function matchPerson(p: Person, q: string, wsName: (s: string) => string): boolean {
  const n = q.trim().toLowerCase();
  if (!n) return true;
  return [p.name, p.email, companyOf(p.email), p.headline ?? "", ...p.workspaces.map(wsName)].some((x) => x.toLowerCase().includes(n));
}
/** "All": Recent → My workspaces → Contacts, each person once, in the first group that holds them. */
export function groupPeople(ps: readonly Person[], now = Date.now()): { key: string; label: string; people: Person[] }[] {
  const seen = new Set<string>();
  const take = (f: (p: Person) => boolean) => ps.filter((p) => !seen.has(p.key) && f(p)).map((p) => { seen.add(p.key); return p; });
  const order = (a: Person, b: Person) => (b.presence === "now" ? 1 : 0) - (a.presence === "now" ? 1 : 0) || a.name.localeCompare(b.name);
  const recent = take((p) => isRecent(p, now)).sort((a, b) => (b.last ?? 0) - (a.last ?? 0));
  const mine = take((p) => p.workspaces.length > 0).sort(order);
  const contacts = take(() => true).sort(order);
  return [{ key: "recent", label: "Recent", people: recent }, { key: "workspaces", label: "My workspaces", people: mine }, { key: "contacts", label: "Contacts", people: contacts }]
    .filter((g) => g.people.length > 0);
}

/** A presence word: "online", "active today", or nothing. (A call state lands with S4's rooms.) */
export const presenceWord = (p: Presence): string => (p === "now" ? "online" : p === "today" ? "active today" : "");

// ── the read, cached for the tab ─────────────────────────────────────────────────────────────
type State = { phase: "idle" | "loading" | "ready" | "failed"; peers: Peer[]; known: KnownPerson[] };
let state: State = { phase: "idle", peers: [], known: [] };
const subs = new Set<() => void>();
const emit = (s: State) => { state = s; subs.forEach((f) => f()); };
export function loadPeople(force = false): void {
  if (!force && (state.phase === "loading" || state.phase === "ready")) return;
  emit({ ...state, phase: "loading" });
  void Promise.all([
    fetch("/api/people/peers", { cache: "no-store" }).then((r) => (r.ok ? r.json() : { people: [] })).then((x: { people?: Peer[] }) => Array.isArray(x.people) ? x.people : []).catch(() => [] as Peer[]),
    listKnownPeople().then((x) => x.people).catch(() => [] as KnownPerson[]),
  ]).then(([peers, known]) => emit({ phase: "ready", peers, known }), () => emit({ ...state, phase: "failed" }));
}
export function usePeopleRead(): State {
  const s = useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => state, () => state);
  useEffect(() => { loadPeople(); }, []);
  return s;
}
/** Test seam. */
export function resetPeople(): void { emit({ phase: "idle", peers: [], known: [] }); }
