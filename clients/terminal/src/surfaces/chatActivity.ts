"use client";
import { useSyncExternalStore } from "react";

// Shared by the stream owner and rail: changing selection does not stop a turn.
const active = new Map<string, boolean>();
const listeners = new Set<() => void>();
const keyOf = (subject: string, session: string) => `${subject}\u0000${session}`;

export function setChatActivity(key: string, state: { busy: boolean; jobs: { queued?: boolean }[] }): void {
  const next = state.busy || state.jobs.some((job) => !job.queued);
  if ((active.get(key) ?? false) === next) return;
  if (next) active.set(key, true);
  else active.delete(key);
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useChatActive(session: string | null, subject = "me"): boolean {
  return useSyncExternalStore(subscribe,
    () => session !== null && (active.get(keyOf(subject, session)) ?? false),
    () => false);
}
