/** NOTICE SOUNDS (0.13.4 S6) — the wire between a live notice and the person's sound policy.
 *
 *  `startNoticeSounds()` (reference-counted, mounted with the toasts) loads the person's policy and
 *  installs the hook `notifications.receive` calls for every new notice. The hook asks the pure
 *  `soundFor()` and plays chime or the call ring. A call's ring stops when its notice is read,
 *  answered (joined / declined) or after 30 s.
 *
 *  Audio is locked until a gesture: when an interruption arrives with audio locked the store raises
 *  `needsEnable`, and the one-time "Enable sounds" prompt shows; any click on the page unlocks too.
 *
 *  `setRoomInView(id)` is the seam the shell calls with the chat/room in front (null when none):
 *  no sound for the room the person is already looking at. */
import { useEffect, useSyncExternalStore } from "react";
import { getNotifications, setNoticeSound, subscribeNotifications, type Notice } from "./notifications";
import { getPolicy, loadPolicy } from "./notificationPolicy";
import { onUnlocked, play, setVolume, soundFor, startRing, stopRing, unlock, unlocked, audioSupported } from "../platform/sounds";

let roomInView: string | null = null;
export function setRoomInView(id: string | null): void { roomInView = id || null; }
export function getRoomInView(): string | null { return roomInView; }

type S = { needsEnable: boolean; ringingId: string | null };
let s: S = { needsEnable: false, ringingId: null };
const subs = new Set<() => void>();
const emit = (next: Partial<S>) => { s = { ...s, ...next }; subs.forEach((f) => f()); };
export const getNoticeSounds = (): S => s;
export function subscribeNoticeSounds(fn: () => void): () => void { subs.add(fn); return () => { subs.delete(fn); }; }

/** The hook itself (exported for tests). */
export function soundNotice(n: Notice): void {
  const p = getPolicy().policy;
  const kind = soundFor(n, { sounds: p.sounds, quiet: p.quiet, roomInView });
  if (!kind) return;
  if (!unlocked()) { if (audioSupported()) emit({ needsEnable: true }); return; }
  setVolume(p.sounds.volume);
  if (kind === "ring") { emit({ ringingId: n.id }); startRing(() => emit({ ringingId: null })); }
  else play(kind);
}

/** Enable sounds from a gesture (the prompt's button, or any click once the prompt is up). */
export function enableSounds(): void {
  unlock();
  setVolume(getPolicy().policy.sounds.volume);
  emit({ needsEnable: false });
}

function stopRingIfAnswered(): void {
  const id = s.ringingId;
  if (!id) return;
  const row = getNotifications().items.find((x) => x.id === id);
  if (!row || row.read || row.state) { stopRing(); emit({ ringingId: null }); }
}

let started = 0;
let stop: (() => void) | null = null;
export function startNoticeSounds(): () => void {
  started += 1;
  if (started === 1) {
    void loadPolicy();
    setNoticeSound(soundNotice);
    const offN = subscribeNotifications(stopRingIfAnswered);
    const offU = onUnlocked(() => emit({ needsEnable: false }));
    const onDown = () => { if (s.needsEnable) enableSounds(); };
    if (typeof window !== "undefined") window.addEventListener("pointerdown", onDown, true);
    stop = () => {
      setNoticeSound(null); offN(); offU(); stopRing();
      if (typeof window !== "undefined") window.removeEventListener("pointerdown", onDown, true);
    };
  }
  return () => {
    started -= 1;
    if (started === 0 && stop) { stop(); stop = null; }
  };
}

export function useNoticeSounds(): S {
  useEffect(() => startNoticeSounds(), []);
  return useSyncExternalStore(subscribeNoticeSounds, getNoticeSounds, getNoticeSounds);
}

/** For tests. */
export function __resetNoticeSounds(): void { roomInView = null; s = { needsEnable: false, ringingId: null }; subs.forEach((f) => f()); }
