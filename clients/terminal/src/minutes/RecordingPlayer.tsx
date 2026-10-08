"use client";
import { useEffect, useRef, useState } from "react";

import { registerPlayback } from "./meetingPlayback";

export function finiteDuration(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}
export function playbackTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor(s / 60) % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** Streaming WebM can report Infinity. Its recording metadata supplies the finite fallback. */
export function RecordingPlayer({ src, duration, onError, meetingId, originMs }: { meetingId?: string; originMs?: number; src: string; duration?: number | null; onError: () => void }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [nativeDuration, setNativeDuration] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const controller = useRef<ReturnType<typeof registerPlayback> | null>(null);
  const errorRef = useRef(onError); errorRef.current = onError;
  useEffect(() => {
    const audio = ref.current;
    if (!meetingId || !audio) return;
    const handle = registerPlayback(meetingId, {
      originMs,
      pause: () => { if (!audio.paused) audio.pause(); },
      seek: seconds => {
        if (Number.isFinite(audio.duration) && seconds >= audio.duration) return;
        try {
          audio.currentTime = seconds; setElapsed(seconds); handle.update(seconds, true);
          void audio.play().catch(() => errorRef.current());
        } catch { errorRef.current(); }
      },
    });
    controller.current = handle;
    return () => { if (!audio.paused) audio.pause(); handle.dispose(); controller.current = null; };
  }, [meetingId, src, originMs]);
  const total = nativeDuration ?? finiteDuration(duration);
  const iconButton = { border: "none", background: "transparent", color: "var(--t1)", cursor: "pointer", padding: "4px 8px" };
  async function toggle() {
    const audio = ref.current;
    if (!audio) return;
    if (!audio.paused) audio.pause();
    else try { await audio.play(); } catch { onError(); }
  }
  return <div role="group" aria-label="Recording player" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", maxWidth: 680, width: "100%", color: "var(--t2)", fontSize: 12 }}>
    <audio ref={ref} aria-label="Meeting audio" preload="metadata" src={src}
      onDurationChange={e => setNativeDuration(finiteDuration(e.currentTarget.duration))}
      onLoadedMetadata={e => setNativeDuration(finiteDuration(e.currentTarget.duration))}
      onTimeUpdate={e => { setElapsed(e.currentTarget.currentTime); controller.current?.update(e.currentTarget.currentTime); }}
      onSeeked={e => { setElapsed(e.currentTarget.currentTime); controller.current?.update(e.currentTarget.currentTime); }}
      onPlay={e => { setPlaying(true); controller.current?.update(e.currentTarget.currentTime, true); }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={onError} />
    <button style={iconButton} aria-label={playing ? "Pause recording" : "Play recording"} onClick={() => void toggle()}>{playing ? "Pause" : "Play"}</button>
    <span aria-label="Playback time" style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>{playbackTime(elapsed)} / {total === null ? "Duration unknown" : playbackTime(total)}</span>
    <input type="range" aria-label="Seek recording" min={0} max={total ?? 0} step={0.1} value={total ? Math.min(elapsed, total) : 0} disabled={!total}
      style={{ flex: "1 1 120px", minWidth: 80, accentColor: "var(--accent)" }}
      onChange={e => { const value = Number(e.target.value); if (ref.current && total) { ref.current.currentTime = Math.min(total, Math.max(0, value)); setElapsed(value); controller.current?.update(value, true); } }} />
    <button style={iconButton} aria-label={muted ? "Unmute recording" : "Mute recording"} onClick={() => { if (ref.current) { ref.current.muted = !muted; setMuted(!muted); } }}>{muted ? "Unmute" : "Mute"}</button>
  </div>;
}
