"use client";
import { useSyncExternalStore } from "react";

type Player = { seek: (seconds: number) => void; pause: () => void; originMs?: number };
type Snapshot = { available: boolean; time: number | null; originMs?: number };
const EMPTY: Snapshot = { available: false, time: null };
const players = new Map<string, Map<symbol, Player>>();
const selected = new Map<string, symbol>();
const snapshots = new Map<string, Snapshot>();
const listeners = new Set<() => void>();
function emit() { listeners.forEach(fn => fn()); }
function choose(meeting: string, id: symbol) {
  selected.set(meeting, id);
  snapshots.set(meeting, { available: true, time: null, originMs: players.get(meeting)?.get(id)?.originMs });
}
/** Coordinates the page header and transcript widget without sharing media or state across meetings. */
export function registerPlayback(meeting: string, player: Player) {
  const id = Symbol();
  const group = players.get(meeting) ?? new Map<symbol, Player>();
  players.set(meeting, group); group.set(id, player);
  if (!selected.has(meeting)) choose(meeting, id);
  emit();
  return {
    update(time: number, activate = false) {
      if (activate) {
        for (const group of players.values()) for (const [other, p] of group) if (other !== id) p.pause();
        choose(meeting, id);
      }
      if (selected.get(meeting) !== id || !Number.isFinite(time)) return;
      snapshots.set(meeting, { available: true, time, originMs: player.originMs }); emit();
    },
    dispose() {
      group.delete(id);
      if (selected.get(meeting) === id) {
        selected.delete(meeting); snapshots.delete(meeting);
        const next = group.keys().next().value;
        if (next) choose(meeting, next);
      }
      if (!group.size) players.delete(meeting);
      emit();
    },
  };
}
export function seekMeeting(meeting: string, seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return;
  const id = selected.get(meeting);
  if (id) players.get(meeting)?.get(id)?.seek(seconds);
}
export function useMeetingPlayback(meeting?: string) {
  return useSyncExternalStore(
    subscribe,
    () => meeting ? snapshots.get(meeting) ?? EMPTY : EMPTY,
    () => EMPTY,
  );
}
function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
/** Absolute epoch clocks need a recording origin. Clock display strings are never audio offsets. */
export function segmentSeconds(segment: { ts?: number | string; tsMs?: number; endMs?: number }, originMs?: number): { start: number; end?: number } | null {
  const absolute = segment.tsMs;
  const relative = typeof segment.ts === "number" && Number.isFinite(segment.ts) && segment.ts < 1e9 ? segment.ts : undefined;
  const start = absolute !== undefined && originMs !== undefined ? (absolute - originMs) / 1000 : relative;
  if (start === undefined || !Number.isFinite(start) || start < 0) return null;
  const end = segment.endMs !== undefined && originMs !== undefined ? (segment.endMs - originMs) / 1000 : undefined;
  return { start, end: end !== undefined && Number.isFinite(end) && end > start ? end : undefined };
}
