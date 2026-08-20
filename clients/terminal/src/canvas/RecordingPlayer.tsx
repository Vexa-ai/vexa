"use client";

/**
 *  RecordingPlayer — plays a finished meeting's recording in the meeting detail.
 *
 *  The recording is ONE chunked HLS stream (ffmpeg-for-all: audio + video muxed, or audio-only). It
 *  plays NATIVELY in Safari/iOS (`<video>`/`<audio src=playlist.m3u8>` — no JS, low battery) and via
 *  hls.js everywhere else. An audio-only recording renders an `<audio>` control; a video recording
 *  renders a `<video>` plus an "Audio only" toggle that plays the same stream with the picture hidden.
 *  Renders nothing when the meeting has no recording, so it is safe to mount for any durable meeting.
 *
 *  Sync (playbackSync): the active element registers a `seekTo` handler and emits its `currentTime`,
 *  anchored to the recording's true start (first_chunk_at = the recorder's ffmpeg t=0).
 *
 *  Combined download (independent of playback): built ON CLICK. GET .../master?type=combined reports
 *  status without building — `disabled` (404, hidden), `available` (show a Download button), `building`
 *  ("Preparing…"), `ready` (a download link). The click sends `&build=1` to start the remux.
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type HlsType from "hls.js"; // TYPE-ONLY (erased at build). hls.js touches `window` at import time,
// so the runtime is dynamically imported inside the client effect below — never during SSR/prerender.
import { usePlaybackSync } from "./playbackSync";

type MediaFile = { id?: number; type?: string; first_chunk_at?: string; has_video?: boolean };
type Recording = { id?: number; meeting_id?: number | string; media_files?: MediaFile[] };
type CombinedResponse = { status?: string; media_file_id?: number | null };
type Resolved = { hlsUrl: string | null; recId: number | null; startMs: number | null; hasVideo: boolean };

const EMPTY: Resolved = { hlsUrl: null, recId: null, startMs: null, hasVideo: false };
const parseMs = (s?: string): number | null => { if (!s) return null; const t = Date.parse(s); return Number.isFinite(t) ? t : null; };

/** A filesystem-safe download name from the meeting title: strip characters illegal on Windows/macOS/
 *  Linux (``/ \ : * ? " < > |`` + control chars), collapse whitespace, cap length, and fall back to
 *  "recording" if the title is empty or sanitizes away to nothing. */
function safeFilename(title: string | undefined, ext: string): string {
  const base = (title || "")
    .replace(/[/\\:*?"<>|\x00-\x1f]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\.+/, "")      // no leading dots (hidden files / traversal)
    .slice(0, 120)
    .trim() || "recording";
  return `${base}.${ext}`;
}

type DownloadState = { status: "hidden" | "available" | "preparing" | "ready"; url: string | null };

/** Trigger a browser download of `url` as `filename` without navigating — a transient <a download>. */
function triggerDownload(url: string, filename: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function combinedStatus(recId: number, build: boolean, signal: AbortSignal): Promise<DownloadState> {
  const res = await fetch(`/api/recordings/${recId}/master?type=combined${build ? "&build=1" : ""}`, { signal, cache: "no-store" });
  if (res.status === 404) return { status: "hidden", url: null };  // disabled or not found
  const m = (await res.json().catch(() => ({}))) as CombinedResponse;
  // READY is signalled by a resolved media_file_id: /master only returns one on the built fall-through
  // (the "building"/"available" branches carry media_file_id:null). Don't gate on status==="ready" alone —
  // the built response historically omitted a status field, which read as "available" and looped the button.
  if (m.media_file_id != null) {
    return { status: "ready", url: `/api/recording-media?rec=${recId}&mf=${m.media_file_id}&type=combined` };
  }
  if (m.status === "building") return { status: "preparing", url: null };
  return { status: "available", url: null };  // enabled, not built yet
}

async function resolveMedia(meetingId: string, signal: AbortSignal): Promise<Resolved> {
  const listRes = await fetch("/api/recordings", { signal, cache: "no-store" });
  if (!listRes.ok) return EMPTY;
  const body = (await listRes.json()) as { recordings?: Recording[] };
  const mine = (body?.recordings ?? []).filter((r) => String(r.meeting_id) === String(meetingId) && r.id != null);
  for (const rec of mine) {
    const hls = (rec.media_files ?? []).find((m) => m.type === "hls");
    if (hls) {
      return {
        hlsUrl: `/api/recordings/${rec.id}/hls/playlist.m3u8`,
        recId: rec.id!,
        startMs: parseMs(hls.first_chunk_at),
        hasVideo: hls.has_video === true,
      };
    }
  }
  return EMPTY;
}

export function RecordingPlayer({ meetingId, title }: { meetingId?: string; title?: string }) {
  const [media, setMedia] = useState<Resolved>(EMPTY);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "none" | "error">("idle");
  const [download, setDownload] = useState<DownloadState>({ status: "hidden", url: null });
  const [preferAudio, setPreferAudio] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const activeRef = useRef<HTMLMediaElement | null>(null);
  const lastPos = useRef<{ sec: number; playing: boolean }>({ sec: 0, playing: false });
  const sync = usePlaybackSync();

  // Audio-only recordings always show the audio control; a video recording shows video unless "Audio only".
  const showAudio = !media.hasVideo || preferAudio;

  useEffect(() => {
    if (!meetingId) { setState("none"); return; }
    const ctrl = new AbortController();
    setState("loading");
    resolveMedia(meetingId, ctrl.signal)
      .then((m) => {
        if (ctrl.signal.aborted) return;
        setMedia(m);
        setState(m.hlsUrl ? "ready" : "none");
      })
      .catch((e) => { if (!ctrl.signal.aborted && e?.name !== "AbortError") setState("error"); });
    return () => ctrl.abort();
  }, [meetingId]);

  // Attach the HLS stream to the ACTIVE element (video or audio). Native `<video>/<audio src>` where the
  // browser demuxes HLS (Safari/iOS), else hls.js. Re-runs on toggle, carrying the playback position over.
  useEffect(() => {
    if (state !== "ready" || !media.hlsUrl) return;
    const el = showAudio ? audioRef.current : videoRef.current;
    if (!el) return;
    activeRef.current = el;
    const url = media.hlsUrl;
    let hls: HlsType | null = null;
    let cancelled = false;
    const carry = () => {
      const pos = lastPos.current;
      if (pos.sec <= 0) return;
      const restore = () => { el.currentTime = pos.sec; if (pos.playing) void el.play(); };
      if (el.readyState >= 1) restore(); else el.addEventListener("loadedmetadata", restore, { once: true });
    };
    if (el.canPlayType("application/vnd.apple.mpegurl")) {
      el.src = url; // native HLS
      carry();
    } else {
      import("hls.js")
        .then(({ default: Hls }) => {
          if (cancelled) return;
          if (!Hls.isSupported()) { el.src = url; carry(); return; }
          hls = new Hls({ enableWorker: true });
          hls.loadSource(url);
          hls.attachMedia(el);
          carry();
        })
        .catch(() => { el.src = url; carry(); });
    }
    return () => { cancelled = true; if (hls) { try { hls.destroy(); } catch { /* noop */ } } };
  }, [state, media.hlsUrl, showAudio]);

  // On load, ask the combined-download STATUS (no build). Hidden (disabled) / available (a button) /
  // building (preparing) / ready (a link). AUTO_COMBINED_RECORDING can make it building/ready already.
  useEffect(() => {
    if (state !== "ready" || media.recId == null) { setDownload({ status: "hidden", url: null }); return; }
    const ctrl = new AbortController();
    combinedStatus(media.recId, false, ctrl.signal)
      .then((d) => { if (!ctrl.signal.aborted) setDownload(d); })
      .catch(() => { /* leave hidden */ });
    return () => ctrl.abort();
  }, [state, media.recId]);

  // The Download button: click starts the remux (?build=1), polls until ready, then downloads in one
  // motion — no second click. A recording built already (auto, or a prior click) resolves to a ready
  // link that downloads directly.
  const onDownloadClick = () => {
    const recId = media.recId;
    if (recId == null || download.status !== "available") return;
    const filename = safeFilename(title, media.hasVideo ? "mp4" : "m4a");
    setDownload({ status: "preparing", url: null });
    const ctrl = new AbortController();
    let attempts = 0;
    const tick = async (build: boolean) => {
      const d = await combinedStatus(recId, build, ctrl.signal).catch(() => ({ status: "preparing", url: null }) as DownloadState);
      setDownload(d);
      if (d.status === "ready" && d.url) { triggerDownload(d.url, filename); return; } // build done → save it
      if (d.status === "preparing" && attempts < 120) { attempts += 1; setTimeout(() => void tick(false), 5000); }
    };
    void tick(true); // build=1 kicks off the remux
  };

  // Transcript sync: seek handler + anchor + position emit, all against the active element.
  useEffect(() => {
    if (!sync) return;
    sync.registerSeek((sec) => { const el = activeRef.current; if (el) { el.currentTime = Math.max(0, sec); void el.play(); } });
    return () => sync.registerSeek(null);
  }, [sync]);

  useEffect(() => { sync?.setRecStartMs(media.startMs); }, [media.startMs, sync]);

  useEffect(() => {
    if (state !== "ready") return;
    const el = showAudio ? audioRef.current : videoRef.current;
    if (!el) return;
    const onTime = () => { lastPos.current = { sec: el.currentTime, playing: !el.paused }; sync?.emitTime(el.currentTime, !el.paused); };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("play", onTime);
    el.addEventListener("pause", onTime);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("play", onTime);
      el.removeEventListener("pause", onTime);
    };
  }, [state, showAudio, sync]);

  if (state === "none" || state === "idle") return null; // no recording → render nothing

  // Shared pill styling so the under-player controls (audio/video toggle + download) read as one set.
  const controlBtn: CSSProperties = {
    cursor: "pointer", background: "transparent", color: "var(--t2)",
    border: "1px solid var(--line2)", borderRadius: 8, padding: "4px 10px",
    fontSize: 12, fontWeight: 600, textDecoration: "none",
    display: "inline-flex", alignItems: "center", gap: 6, lineHeight: 1.4,
  };
  const dlName = safeFilename(title, media.hasVideo ? "mp4" : "m4a");

  return (
    <div style={{ padding: "8px 0", display: "flex", flexDirection: "column", gap: 8 }}>
      {state === "loading" && <div style={{ color: "var(--t3)", fontSize: 13 }}>Preparing recording…</div>}
      {state === "error" && <div style={{ color: "var(--red, #c00)", fontSize: 13 }}>Recording could not be loaded.</div>}
      {state === "ready" && (
        <>
          {showAudio ? (
            <audio ref={audioRef} controls preload="metadata" style={{ width: "100%" }} />
          ) : (
            <video ref={videoRef} controls preload="metadata" playsInline style={{ width: "100%", maxHeight: "60vh", borderRadius: 10, background: "#000" }} />
          )}
          {/* Controls UNDER the player: the audio/video toggle sits next to the download, matched styling. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {/* An "Audio only" toggle only makes sense when the recording has video. */}
            {media.hasVideo && (
              <button
                type="button"
                onClick={() => setPreferAudio((p) => !p)}
                title={preferAudio ? "Show the video" : "Play just the audio"}
                style={controlBtn}
              >
                {preferAudio ? "Show video" : "Audio only"}
              </button>
            )}
            {download.status === "available" && (
              <button type="button" onClick={onDownloadClick} style={controlBtn}>Download</button>
            )}
            {download.status === "preparing" && (
              <span style={{ ...controlBtn, cursor: "default", color: "var(--t3)" }}>Preparing…</span>
            )}
            {download.status === "ready" && download.url && (
              <a href={download.url} download={dlName} style={controlBtn}>Download</a>
            )}
          </div>
        </>
      )}
    </div>
  );
}
