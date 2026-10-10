"use client";
import { Download, Trash2 } from "lucide-react";
import { Modal } from "../ui-kit/Modal";
import { MediaIconButton } from "./MediaIconButton";
import { markMeetingDeleted, useMeetingDeletedHere } from "./meetingDeletion";
import { RecordingPlayer, finiteDuration } from "./RecordingPlayer";
import { audioDownloadUrl, audioExtension, audioFilename, downloadAudio } from "./audioDownload";
import { useEffect, useState } from "react";
import { useLiveMeetings, useLiveMeetingsConnection, refreshMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";

type Track = { id: number; duration_seconds?: number; media_files?: { capture_started_at_ms?: number; id: number; type: string; format?: string; duration_seconds?: number }[] };
const button = { background: "transparent", color: "var(--t2)", border: "1px solid var(--line)", borderRadius: 6, padding: "4px 9px", fontSize: 12, cursor: "pointer" };
const terminal = new Set(["completed", "failed", "stopped"]);
const running = new Set(["requested", "joining", "awaiting_admission", "needs_help", "active", "stopping"]);

const HINT = "click transcript text to play from there";

/** The meeting page's player row (and, where there is no header to hold it, the Delete control).
 *  `showDelete={false}` when the page header already carries Delete (`MeetingDeleteButton`). */
export function MeetingControls({ meetingId, showBot = true, showDelete = true }: { meetingId: string; showBot?: boolean; showDelete?: boolean }) {
  const meetings = useLiveMeetings();
  const connected = useLiveMeetingsConnection();
  const meeting = meetings.find(m => m.id === meetingId || m.native_id === meetingId);
  return meeting ? <Controls key={meeting.id} meeting={meeting} connected={connected} showBot={showBot} showDelete={showDelete} /> : null;
}

function statusOf(m: MeetingMock) { return m.live_status || (m.status === "live" ? "active" : "completed"); }

/** Delete as an icon button: danger colour on hover only, and the typed confirmation before
 *  anything is removed. `size="header"` matches the document header's icon group. */
export function MeetingDeleteButton({ meetingId, size }: { meetingId: string; size?: "header" }) {
  const meetings = useLiveMeetings();
  const m = meetings.find(x => x.id === meetingId || x.native_id === meetingId);
  const deletedHere = useMeetingDeletedHere(m?.id ?? "");
  const [confirm, setConfirm] = useState(false);
  const [confirmationText, setConfirmationText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!m || m.shared || deletedHere || m.artifacts_deleted || !terminal.has(statusOf(m))) return null;
  const close = () => { if (!busy) { setConfirm(false); setConfirmationText(""); setError(""); } };
  async function remove() {
    if (!m || confirmationText !== "delete") return;
    setBusy(true); setError("");
    try {
      const r = await fetch(`/api/meetings/${encodeURIComponent(m.id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error("Could not delete meeting data. Retry.");
      markMeetingDeleted(m.id); setConfirm(false); setConfirmationText("");
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); }
  }
  return <>
    <MediaIconButton label="Delete meeting data" tone="danger" size={size} disabled={busy} onClick={() => { setConfirmationText(""); setConfirm(true); }}>
      <Trash2 size={size ? 14 : 16} strokeWidth={1.75} aria-hidden />
    </MediaIconButton>
    {confirm && <Modal title="Delete meeting data?" onClose={close}>
      <p style={{ fontSize: 13, color: "var(--t2)", lineHeight: 1.5 }}>Permanently delete this meeting’s audio, transcript and captured fixtures, including promoted fixtures. Saved pages are kept.</p>
      <form onSubmit={e => { e.preventDefault(); if (!busy && confirmationText === "delete") void remove(); }}>
        <label style={{ display: "block", fontSize: 13 }}>Type <strong>delete</strong> to confirm
          <input autoFocus aria-label="Type delete to confirm" autoComplete="off" spellCheck={false} disabled={busy} value={confirmationText} onChange={e => setConfirmationText(e.target.value)} style={{ display: "block", boxSizing: "border-box", width: "100%", marginTop: 8, padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--bg)", color: "var(--t1)" }} />
        </label>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
          <button type="button" style={button} disabled={busy} onClick={close}>Cancel</button>
          <button type="submit" disabled={busy || confirmationText !== "delete"} style={{ ...button, background: "var(--danger)", color: "white", border: "none", opacity: busy || confirmationText !== "delete" ? 0.45 : 1 }}>{busy ? "Deleting…" : "Delete permanently"}</button>
        </div>
        {error && <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>{error}</p>}
      </form>
    </Modal>}
  </>;
}

function Controls({ meeting: m, connected, showBot, showDelete }: { meeting: MeetingMock; connected: boolean; showBot: boolean; showDelete: boolean }) {
  const status = statusOf(m);
  const finished = terminal.has(status);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const deletedHere = useMeetingDeletedHere(m.id);
  // The server row is the truth after a reload or on another tab; the local flag covers the moment
  // between this delete answering and the meetings list refreshing.
  const deleted = deletedHere || !!m.artifacts_deleted;
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState("");
  const [saveError, setSaveError] = useState("");
  async function save(t: { recording: number; media: number; format?: string }) {
    const key = `${t.recording}/${t.media}`;
    setSaving(key); setSaveError("");
    try { await downloadAudio(audioDownloadUrl(t.recording, t.media), type => audioFilename(m, audioExtension(type, t.format))); }
    catch (e) { setSaveError(e instanceof Error ? e.message : "Could not download audio. Retry."); }
    finally { setSaving(""); }
  }
  useEffect(() => {
    if (!finished || deleted) { setTracks([]); return; }
    const controller = new AbortController();
    setLoading(true); setError("");
    fetch(`/api/recordings?meeting_id=${encodeURIComponent(m.id)}&limit=200`, { signal: controller.signal })
      .then(async r => { if (!r.ok) throw new Error("Could not load recording"); return r.json(); })
      .then(data => { if (!controller.signal.aborted) setTracks(data.recordings || []); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [m.id, m.has_recording, finished, deleted, retry]);
  async function stopBot() {
    setBusy(true); setError("");
    const platform = m.platform === "Google Meet" ? "google_meet" : m.platform.toLowerCase().replace(/\s+/g, "_");
    try {
      const r = await fetch(`/api/bots/${platform}/${encodeURIComponent(m.native_id || m.id)}`, { method: "DELETE" });
      if (!r.ok) throw new Error("Could not stop bot. Retry.");
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); }
  }
  const audio = tracks.flatMap(t => (t.media_files || []).filter(f => f.type === "audio").map(f => ({ recording: t.id, media: f.id, format: f.format, origin: finiteDuration(f.capture_started_at_ms), duration: finiteDuration(f.duration_seconds) ?? finiteDuration(t.duration_seconds) })));
  const showStop = showBot && running.has(status) && !m.shared;
  const approximate = audio.some(t => !t.origin) ? "older recording timing is approximate" : "";
  return <section aria-label="Meeting controls" style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
    {showStop && <div><button style={button} disabled={busy || !connected || status === "stopping"} onClick={() => void stopBot()}>{status === "stopping" ? "Stopping…" : "Stop bot"}</button></div>}
    {deleted && <span role="status" style={{ fontSize: 12, color: "var(--t3)" }}>Meeting audio, transcript and fixtures deleted. Saved pages are kept.</span>}
    {finished && !deleted && <>
      {loading ? <span role="status" style={{ fontSize: 12, color: "var(--t3)" }}>Loading recording…</span>
        : !audio.length && !error ? <div style={{ display: "flex", alignItems: "center", gap: 6 }}><span style={{ fontSize: 12, color: "var(--t3)" }}>No audio recording available.</span>{showDelete && <MeetingDeleteButton meetingId={m.id} />}</div> : null}
      {audio.map(t => <RecordingPlayer key={`${t.recording}/${t.media}/${retry}`} meetingId={m.id}
        originMs={t.origin ?? (m.start_time && Number.isFinite(Date.parse(m.start_time)) ? Date.parse(m.start_time) : undefined)}
        duration={t.duration} hint={[HINT, approximate].filter(Boolean).join("; ")}
        src={`/api/recordings/${t.recording}/media/${t.media}/raw?type=audio`} onError={() => setError("Audio could not be played. Retry loading the recording.")}
        actions={<>
          <MediaIconButton label="Download audio" tooltip={saving === `${t.recording}/${t.media}` ? "Downloading…" : "Download audio"} disabled={!!saving} onClick={() => void save(t)}>
            <Download size={16} strokeWidth={1.75} aria-hidden />
          </MediaIconButton>
          {showDelete && <MeetingDeleteButton meetingId={m.id} />}
        </>} />)}
      {saveError && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>{saveError}</div>}
    </>}
    {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>{error} {finished && <button style={button} onClick={() => setRetry(n => n + 1)}>Retry</button>}</div>}
  </section>;
}
