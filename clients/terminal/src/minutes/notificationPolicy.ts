/** THE PERSON'S NOTIFICATION POLICY (0.13.4 S6) — how and when Vexa may interrupt them.
 *
 *  Identity-owned, read and written through the catch-all `/api/user/*` → gateway `/user/*`:
 *    GET /api/user/notifications            → { notifications: Policy, timezone, limits }
 *    PUT /api/user/notifications  (partial) → the same shape; 422 on a bad value
 *  One module-level store per tab, shared by Settings → Notifications and the sound path. A change
 *  is shown at once and rolled back (with one line) if the server refuses it. */
import { useEffect, useSyncExternalStore } from "react";

export type Level = "all" | "mentions" | "none";
export type Policy = {
  channels: { email: boolean; whatsapp: boolean };
  idle_min: number;
  digest: "hourly" | "daily" | "off";
  digest_at: string;
  quiet: { on: boolean; from: string; to: string };
  budget: number;
  rooms: Record<string, Level>;
  sounds: { on: boolean; chime: boolean; tick: boolean; ring: boolean; volume: number };
};
export type Limits = { idle_floor_min: number; budget_ceiling: number };

export const DEFAULT_POLICY: Policy = {
  channels: { email: true, whatsapp: false },
  idle_min: 10,
  digest: "hourly",
  digest_at: "18:00",
  quiet: { on: true, from: "22:00", to: "08:00" },
  budget: 6,
  rooms: {},
  sounds: { on: true, chime: true, tick: true, ring: true, volume: 0.6 },
};
export const DEFAULT_LIMITS: Limits = { idle_floor_min: 5, budget_ceiling: 12 };

export type PolicyState = { phase: "idle" | "loading" | "ready" | "off"; policy: Policy; timezone: string; limits: Limits };
let state: PolicyState = { phase: "idle", policy: DEFAULT_POLICY, timezone: "", limits: DEFAULT_LIMITS };
let inflight: Promise<void> | null = null;
const subs = new Set<() => void>();
const emit = (s: PolicyState) => { state = s; subs.forEach((f) => f()); };

/** Merge what the server answered over the defaults, so a missing key never breaks a screen. */
export function normalizePolicy(raw: unknown): Policy {
  const p = (raw && typeof raw === "object" ? raw : {}) as Partial<Policy>;
  const d = DEFAULT_POLICY;
  return {
    channels: { ...d.channels, ...(p.channels ?? {}) },
    idle_min: typeof p.idle_min === "number" ? p.idle_min : d.idle_min,
    digest: p.digest === "daily" || p.digest === "off" || p.digest === "hourly" ? p.digest : d.digest,
    digest_at: typeof p.digest_at === "string" ? p.digest_at : d.digest_at,
    quiet: { ...d.quiet, ...(p.quiet ?? {}) },
    budget: typeof p.budget === "number" ? p.budget : d.budget,
    rooms: p.rooms && typeof p.rooms === "object" ? { ...p.rooms } : {},
    sounds: { ...d.sounds, ...(p.sounds ?? {}) },
  };
}

function absorb(d: unknown): void {
  const o = (d && typeof d === "object" ? d : {}) as { notifications?: unknown; timezone?: unknown; limits?: Partial<Limits> };
  emit({
    phase: "ready",
    policy: normalizePolicy(o.notifications),
    timezone: typeof o.timezone === "string" ? o.timezone : "",
    limits: { ...DEFAULT_LIMITS, ...(o.limits ?? {}) },
  });
}

export function getPolicy(): PolicyState { return state; }
export function subscribePolicy(fn: () => void): () => void { subs.add(fn); return () => { subs.delete(fn); }; }

export function loadPolicy(force = false): Promise<void> {
  if (inflight) return inflight;
  if (!force && (state.phase === "ready" || state.phase === "off")) return Promise.resolve();
  emit({ ...state, phase: "loading" });
  inflight = (async () => {
    try {
      const r = await fetch("/api/user/notifications", { cache: "no-store" });
      if (!r.ok) { emit({ ...state, phase: "off" }); return; }
      absorb(await r.json());
    } catch {
      emit({ ...state, phase: "off" });
    } finally { inflight = null; }
  })();
  return inflight;
}

/** Change part of the policy (any subset of top-level keys). Shown at once; on a refusal the
 *  previous policy comes back and `onRefused` is told (Settings shows the toast). */
export async function savePolicy(patch: Partial<Policy>, onRefused?: () => void): Promise<boolean> {
  const before = state.policy;
  // `rooms` merges, as the server merges it: a patch names only the rooms it changes
  const merged = { ...before, ...patch, ...(patch.rooms ? { rooms: { ...before.rooms, ...patch.rooms } } : {}) };
  emit({ ...state, policy: merged });
  try {
    const r = await fetch("/api/user/notifications", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error(String(r.status));
    absorb(await r.json().catch(() => ({ notifications: merged, timezone: state.timezone, limits: state.limits })));
    return true;
  } catch {
    emit({ ...state, policy: before });
    onRefused?.();
    return false;
  }
}

export function usePolicy(): PolicyState {
  useEffect(() => { void loadPolicy(); }, []);
  return useSyncExternalStore(subscribePolicy, getPolicy, getPolicy);
}

/** Test seam. */
export function __resetPolicy(): void { inflight = null; emit({ phase: "idle", policy: DEFAULT_POLICY, timezone: "", limits: DEFAULT_LIMITS }); }
