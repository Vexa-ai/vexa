"use client";
/** playbackSync — the thin bridge between the recording player and the transcript, which are otherwise
 *  independent siblings under the meeting <main>. It carries NO per-tick state through React (that would
 *  re-render a long transcript ~4×/s): the player REGISTERS a seek handler and EMITS its time to a
 *  listener set; the transcript SUBSCRIBES and re-renders only when the active line actually changes.
 *
 *  Time base is RELATIVE SECONDS from the start of the meeting/recording — the value a media element's
 *  `currentTime` already speaks and the value a transcript line carries as `ts`. (Recording and
 *  transcription both begin when the bot joins, so the two clocks align within a second or two.) */
import { createContext, useContext, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

type TimeListener = (sec: number, playing: boolean) => void;

export interface PlaybackSync {
  /** The transcript's scroll container (the meeting <main>) — auto-scroll + scroll-into-view bounds. */
  scrollerRef: RefObject<HTMLElement | null>;
  /** True once a player has registered a seek handler (a recording is loaded and playable). */
  hasPlayer: boolean;
  /** Wall-clock epoch (ms) the recording's currentTime=0 maps to — the player computes it once media
   *  metadata loads (end_time − played-file duration) so the transcript anchors to the RECORDING's true
   *  start, not the first spoken line. Null until known (falls back to the earliest line). */
  recStartMs: number | null;
  /** The player publishes its anchor here (re-computed on an audio/video toggle — durations differ). */
  setRecStartMs(ms: number | null): void;
  /** The player registers HOW to seek (relative seconds); pass null on unmount. */
  registerSeek(fn: ((sec: number) => void) | null): void;
  /** Transcript → player: seek to a relative-second offset (and play). */
  seekTo(sec: number): void;
  /** Player → subscribers: the current position (relative seconds) + whether it's playing. */
  emitTime(sec: number, playing: boolean): void;
  /** Subscribe to time updates; returns an unsubscribe. Fires on every tick — the caller decides what
   *  is worth a re-render (the transcript re-renders only when its active line changes). */
  subscribeTime(cb: TimeListener): () => void;
}

const Ctx = createContext<PlaybackSync | null>(null);

/** Nullable — a transcript rendered outside a meeting canvas simply has no player to sync with. */
export const usePlaybackSync = (): PlaybackSync | null => useContext(Ctx);

export function PlaybackSyncProvider({
  scrollerRef,
  children,
}: {
  scrollerRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const seekRef = useRef<((sec: number) => void) | null>(null);
  const listeners = useRef<Set<TimeListener>>(new Set());
  // A boolean (not a ref) so the transcript re-renders ONCE when a player appears/disappears and can
  // flip its lines between plain and clickable. Flips at most a couple of times per meeting.
  const [hasPlayer, setHasPlayer] = useState(false);
  // The recording anchor re-renders the transcript when it resolves/changes (media load, toggle) so the
  // active-line + click math re-derives against the correct start. Rare.
  const [recStartMs, setRecStartMs] = useState<number | null>(null);

  const value = useMemo<PlaybackSync>(() => ({
    scrollerRef,
    hasPlayer,
    recStartMs,
    setRecStartMs,
    registerSeek: (fn) => { seekRef.current = fn; setHasPlayer(fn != null); },
    seekTo: (sec) => seekRef.current?.(sec),
    emitTime: (sec, playing) => { for (const l of listeners.current) l(sec, playing); },
    subscribeTime: (cb) => { listeners.current.add(cb); return () => { listeners.current.delete(cb); }; },
  }), [scrollerRef, hasPlayer, recStartMs]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
