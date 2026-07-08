"use client";
/** chatContext — the terminal-state CONTEXT BUNDLE the chat sends with each turn (slice 1).
 *
 *  Pure helpers (unit-tested): `focusTarget` maps the active center tab to the wire focus
 *  payload; `scheduleEligible` is the ambient-digest surface gate (mirrored server-side);
 *  `buildChatContext` assembles the wire object. The server derives the schedule itself —
 *  the client contributes only its timezone, which surface it's on, and the user's explicit
 *  include toggles (persisted per chat session).
 */
import type { ActiveTab } from "../workbench/layout";

export type FocusPayload =
  | { kind: "meeting"; native_id: string; meeting_id?: string; platform?: string;
      status?: string; title?: string; scheduled_at?: string; workspace_id?: string }
  | { kind: "file"; ref: string }
  | { kind: "workspace"; slug: string; shared?: boolean }
  | { kind: "today" };

export interface ChatContext {
  tz?: string;
  surface?: { list?: string; tab?: { kind: string } };
  focus?: FocusPayload | null;
  include?: { schedule?: boolean };
}

/** The active tab → the focus payload skeleton (meeting enrichment happens in chat.tsx where
 *  the live-meetings store is at hand; the server re-derives authoritative fields anyway). */
export function focusTarget(tab: ActiveTab | null): FocusPayload | null {
  if (!tab) return null;
  const p = (tab.params ?? {}) as Record<string, unknown>;
  if ((tab.kind === "doc" || tab.kind === "file") && typeof p.path === "string" && p.path) {
    return { kind: "file", ref: `@file:${p.path}` };
  }
  if ((tab.kind === "meeting" || tab.kind === "meetingPrep")) {
    const id = typeof p.meetingId === "string" && p.meetingId ? p.meetingId : null;
    if (id) return { kind: "meeting", native_id: id };
  }
  if (tab.kind === "workspace" && typeof p.slug === "string" && p.slug) {
    return { kind: "workspace", slug: p.slug, shared: p.shared === true || undefined };
  }
  if (tab.kind === "today") return { kind: "today" };
  return null;
}

/** The ambient-digest surface gate — MUST mirror the server's `_ambient_gated` default. */
export function scheduleEligible(activeList: string | null, tab: ActiveTab | null): boolean {
  if (activeList === "meetings") return true;
  const kind = tab?.kind ?? "";
  return kind === "today" || kind === "meeting" || kind === "meetingPrep";
}

export function browserTz(): string | undefined {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined; }
  catch { return undefined; }
}

export function buildChatContext(args: {
  activeList: string | null;
  activeTab: ActiveTab | null;
  focus: FocusPayload | null;          // the (possibly enriched/cleared) focus chat.tsx resolved
  includeSchedule: boolean | null;     // explicit user toggle; null = surface-gated default
}): ChatContext {
  const ctx: ChatContext = {
    tz: browserTz(),
    surface: {
      ...(args.activeList ? { list: args.activeList } : {}),
      ...(args.activeTab ? { tab: { kind: args.activeTab.kind } } : {}),
    },
    focus: args.focus,                 // null is meaningful: the user cleared the chip
  };
  if (args.includeSchedule !== null) ctx.include = { schedule: args.includeSchedule };
  return ctx;
}

// ── per-session include-toggle persistence ─────────────────────────────────────────────
const INCLUDE_KEY = "vexa.terminal.chat.include.v1";

export function readIncludeSchedule(session: string): boolean | null {
  try {
    const raw = window.localStorage.getItem(`${INCLUDE_KEY}:${session}`);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return typeof v.schedule === "boolean" ? v.schedule : null;
  } catch { return null; }
}

export function writeIncludeSchedule(session: string, value: boolean | null): void {
  try {
    const key = `${INCLUDE_KEY}:${session}`;
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify({ schedule: value }));
  } catch { /* private mode etc. — the toggle just doesn't persist */ }
}
