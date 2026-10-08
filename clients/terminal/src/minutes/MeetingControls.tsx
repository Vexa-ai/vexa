"use client";
import { Modal } from "../ui-kit/Modal";
import { Icon } from "../ui-kit";
import { RecordingPlayer, finiteDuration } from "./RecordingPlayer";
import { useEffect, useState } from "react";
import { useLiveMeetings, useLiveMeetingsConnection, refreshMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";

type Track = { id: number; duration_seconds?: number; media_files?: { id: number; type: string; duration_seconds?: number }[] };
const button = { background: "transparent", color: "var(--t2)", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 9px", fontSize: 12, cursor: "pointer" };
const terminal = new Set(["completed", "failed", "stopped"]);
const running = new Set(["requested", "joining", "awaiting_admission", "needs_help", "active", "stopping"]);

export function MeetingControls({ meetingId, showBot = true }: { meetingId: string; showBot?: boolean }) {
  const meetings = useLiveMeetings();
  const connected = useLiveMeetingsConnection();
  const meeting = meetings.find(m => m.id === meetingId || m.native_id === meetingId);
  return meeting ? <Controls key={meeting.id} meeting={meeting} connected={connected} showBot={showBot} /> : null;
}

function Controls({ meeting: m, connected, showBot }: { meeting: MeetingMock; connected: boolean; showBot: boolean }) {
  const status = m.live_status || (m.status === "live" ? "active" : "completed");
  const finished = terminal.has(status);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [confirmationText, setConfirmationText] = useState("");
  const [deleted, setDeleted] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!finished || deleted) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    fetch(`/api/recordings?meeting_id=${encodeURIComponent(m.id)}&limit=200`, { signal: controller.signal })
      .then(async r => { if (!r.ok) throw new Error("Could not load recording"); return r.json(); })
      .then(data => { if (!controller.signal.aborted) setTracks(data.recordings || []); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [m.id, m.has_recording, finished, deleted, retry]);
  async function mutate(kind: "stop" | "delete") {
    if (kind === "delete" && confirmationText !== "delete") return;
    setBusy(true); setError("");
    const platform = m.platform === "Google Meet" ? "google_meet" : m.platform.toLowerCase().replace(/\s+/g, "_");
    const path = kind === "stop" ? `/api/bots/${platform}/${encodeURIComponent(m.native_id || m.id)}` : `/api/meetings/${encodeURIComponent(m.id)}`;
    try {
      const r = await fetch(path, { method: "DELETE" });
      if (!r.ok) throw new Error(kind === "stop" ? "Could not stop bot. Retry." : "Could not delete meeting data. Retry.");
      if (kind === "delete") { setDeleted(true); setTracks([]); setConfirm(false); setConfirmationText(""); }
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); }
  }
  const audio = tracks.flatMap(t => (t.media_files || []).filter(f => f.type === "audio").map(f => ({ recording: t.id, media: f.id, duration: finiteDuration(f.duration_seconds) ?? finiteDuration(t.duration_seconds) })));
  return <section aria-label="Meeting controls" style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      {showBot && running.has(status) && !m.shared && <button style={button} disabled={busy || !connected || status === "stopping"} onClick={() => void mutate("stop")}>{status === "stopping" ? "Stopping…" : "Stop bot"}</button>}
      {finished && !deleted && !m.shared && <button aria-label="Delete meeting data" title="Delete meeting data" style={{ ...button, color: "var(--danger)", display: "inline-flex", alignItems: "center", gap: 6 }} disabled={busy} onClick={() => { setConfirmationText(""); setConfirm(true); }}><Icon name="trash" size={14} /> Delete</button>}
      {deleted && <span role="status">Meeting audio, transcript and fixtures deleted. Saved pages are kept.</span>}
    </div>
    {confirm && <Modal title="Delete meeting data?" onClose={() => { if (!busy) { setConfirm(false); setConfirmationText(""); } }}>
      <p style={{ fontSize: 13, color: "var(--t2)", lineHeight: 1.5 }}>Permanently delete this meeting’s audio, transcript and captured fixtures, including promoted fixtures. Saved pages are kept.</p>
      <form onSubmit={e => { e.preventDefault(); if (!busy && confirmationText === "delete") void mutate("delete"); }}>
        <label style={{ display: "block", fontSize: 13 }}>Type <strong>delete</strong> to confirm
          <input autoFocus aria-label="Type delete to confirm" autoComplete="off" spellCheck={false} disabled={busy} value={confirmationText} onChange={e => setConfirmationText(e.target.value)} style={{ display: "block", boxSizing: "border-box", width: "100%", marginTop: 8, padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--bg)", color: "var(--t1)" }} />
        </label>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
          <button type="button" style={button} disabled={busy} onClick={() => { setConfirm(false); setConfirmationText(""); }}>Cancel</button>
          <button type="submit" disabled={busy || confirmationText !== "delete"} style={{ ...button, background: "var(--danger)", color: "white", border: "none", opacity: busy || confirmationText !== "delete" ? 0.45 : 1 }}>{busy ? "Deleting…" : "Delete permanently"}</button>
        </div>
        {error && <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>{error}</p>}
      </form>
    </Modal>}
    {finished && !deleted && <>
      {loading ? <span role="status">Loading recording…</span> : !audio.length && !error ? <span style={{ fontSize: 12, color: "var(--t3)" }}>No audio recording available.</span> : null}
      {audio.map(t => <RecordingPlayer key={`${t.recording}/${t.media}/${retry}`} duration={t.duration} src={`/api/recordings/${t.recording}/media/${t.media}/raw?type=audio`} onError={() => setError("Audio could not be played. Retry loading the recording.")} />)}
    </>}
    {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>{error} {finished && <button style={button} onClick={() => setRetry(n => n + 1)}>Retry</button>}</div>}
  </section>;
}
