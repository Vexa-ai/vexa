"use client";
/** RecordingPlayer — play a meeting back in the browser.
 *
 *  Recordings were captured, stored and downloadable over the API since 0.12, but nothing in the
 *  workbench could open one: there was no player element anywhere, and `recordings` was not on the
 *  proxy's meetings-domain list, so the terminal could not even reach the endpoint. This is that
 *  missing hop, nothing more — the bytes, the URLs and the range-request support already existed.
 *
 *  The browser does the work: a native <video>/<audio> element streams from
 *  /api/recordings/{id}/master?type=… and SEEKS with range requests, which the proxy forwards
 *  verbatim. No media library, no blob download, no buffering of a multi-hundred-MB file in JS.
 */
import { useState } from "react";
import type { RecordingRef } from "./meetingModel";

/** The playable tracks of a meeting, preferring video when the deployment captured it. */
function tracksOf(recordings: RecordingRef[] | undefined): { video?: string; audio?: string } {
  const out: { video?: string; audio?: string } = {};
  for (const r of recordings ?? []) {
    if (!out.video && r.playback_url?.video) out.video = `/api${r.playback_url.video}`;
    if (!out.audio && r.playback_url?.audio) out.audio = `/api${r.playback_url.audio}`;
  }
  return out;
}

export function RecordingPlayer({ recordings }: { recordings?: RecordingRef[] }) {
  const { video, audio } = tracksOf(recordings);
  // Video when we have it, audio otherwise — and let the user switch when both exist, because the
  // audio is a fraction of the size and is often all someone wants.
  const [mode, setMode] = useState<"video" | "audio">(video ? "video" : "audio");
  const [failed, setFailed] = useState(false);
  if (!video && !audio) return null;

  const src = mode === "video" ? video : audio;
  if (!src) return null;

  const tab = (on: boolean): React.CSSProperties => ({
    padding: "3px 9px", borderRadius: 6, fontSize: 11.5, cursor: "pointer", border: "none",
    color: on ? "var(--t1)" : "var(--t2)", background: on ? "var(--panel2)" : "transparent",
  });

  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase" }}>Recording</span>
        <div style={{ flex: 1 }} />
        {video && audio ? (
          <div style={{ display: "flex", gap: 2 }}>
            <button style={tab(mode === "video")} onClick={() => { setFailed(false); setMode("video"); }}>Video</button>
            <button style={tab(mode === "audio")} onClick={() => { setFailed(false); setMode("audio"); }}>Audio</button>
          </div>
        ) : null}
        <a href={src} download style={{ fontSize: 11.5, color: "var(--t2)", textDecoration: "none" }}>Download</a>
      </div>

      {failed ? (
        // P18/P21: a player that silently shows nothing looks exactly like a meeting that was
        // never recorded. Say which thing failed, and leave the download link as the way out.
        <div style={{ fontSize: 12, color: "var(--warn)", lineHeight: 1.5 }}>
          This {mode} recording could not be played here — the file may still be assembling, or your
          browser may not support its format. Download it to play locally.
        </div>
      ) : mode === "video" ? (
        <video key={src} src={src} controls preload="metadata" onError={() => setFailed(true)}
          style={{ width: "100%", maxHeight: 420, borderRadius: 6, background: "var(--panel2)", display: "block" }} />
      ) : (
        <audio key={src} src={src} controls preload="metadata" onError={() => setFailed(true)}
          style={{ width: "100%", display: "block" }} />
      )}
    </div>
  );
}
