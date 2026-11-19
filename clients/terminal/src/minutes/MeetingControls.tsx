"use client";
import { Download, Trash2 } from "lucide-react";
import { Modal } from "../ui-kit/Modal";
import { MediaIconButton } from "./MediaIconButton";
import { deleteMeetingData, markMeetingDeleted, useMeetingDeletedHere } from "./meetingDeletion";
import { ConfirmDialog } from "../ui-kit";
import { RecordingPlayer, finiteDuration } from "./RecordingPlayer";
import { audioDownloadUrl, audioExtension, audioFilename, downloadAudio } from "./audioDownload";
import { useEffect, useState } from "react";
import { useLiveMeetings, useLiveMeetingsConnection, refreshMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import { clock, endVoiceNote, pauseVoiceNote, stopVoiceNote, useVoiceNote } from "./voiceNote";
import "./minutes.css";

type Track = { id: number; duration_seconds?: number; media_files?: { capture_started_at_ms?: number; id: number; type: string; format?: string; duration_seconds?: number }[] };
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
/** A voice note recording IN THIS TAB: the running time (with the cap), Pause/Resume and Stop. Shown in
 *  the meeting controls and in the meeting view's own header (founder 2026-10-13: the page itself
 *  says it is recording and stops it). Renders nothing unless `nativeId` is the note this tab records. */
export function VoiceNoteRecorderBar({ nativeId }: { nativeId?: string } = {}) {
  const vn = useVoiceNote();
  if (nativeId !== undefined && vn.nativeId !== nativeId) return null;
  if (vn.phase !== "recording" && vn.phase !== "stopping") return null;
  return <div data-voice-note-bar style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
    <span role="status" data-voice-note-clock className="vx-mc-clock" style={{ fontVariantNumeric: "tabular-nums", display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span aria-hidden className="vx-mc-rec-dot" data-paused={vn.paused ? "" : undefined} style={{ width: 8, height: 8, opacity: vn.paused ? 1 : 0.4 + Math.min(0.6, vn.level * 2) }} />
      {vn.paused ? "Paused" : "Recording"} {clock(vn.sentSec)}{vn.maxSec ? ` / ${clock(vn.maxSec)}` : ""}
    </span>
    <button className="vx-mc-btn" disabled={vn.phase !== "recording"} onClick={() => pauseVoiceNote(!vn.paused)}>{vn.paused ? "Resume" : "Pause"}</button>
    <button className="vx-mc-btn" disabled={vn.phase !== "recording"} onClick={() => void stopVoiceNote()}>{vn.phase === "stopping" ? "Saving…" : "Stop"}</button>
  </div>;
}

/** The statuses a meeting is still happening in — Delete waits until it is not. A meeting that is
 *  SAVING (`stopping`) offers Delete and waits for the save to finish before deleting. */
const happening = new Set(["requested", "joining", "awaiting_admission", "needs_help", "active"]);

/** DELETE, ON THE MEETING (#1783, founder: "where is delete?" on a voice note page). A plain button
 *  beside Export, one confirmation naming what goes. A meeting still saving is waited for, not
 *  refused; after the delete the page closes and the row leaves the lists. */
export function MeetingDeleteButton({ meetingId }: { meetingId: string; size?: "header" }) {
  const meetings = useLiveMeetings();
  const m = meetings.find(x => x.id === meetingId || x.native_id === meetingId);
  const deletedHere = useMeetingDeletedHere(m?.id ?? "");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  if (!m || m.shared || deletedHere || m.artifacts_deleted || happening.has(statusOf(m))) return null;
  const voice = m.platform === "voice_note";
  async function remove() {
    if (!m) return;
    setBusy(true); setError(""); setWaiting(false);
    try {
      await deleteMeetingData(m.id, { onWaiting: () => setWaiting(true), native: m.native_id });
      setConfirm(false);
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); setWaiting(false); }
  }
  return <>
    <button type="button" className="bun-btn" data-tone="danger" data-meeting-delete aria-label="Delete meeting data"
      disabled={busy} onClick={() => { setError(""); setConfirm(true); }}>
      <Trash2 size={14} strokeWidth={1.75} aria-hidden /> Delete
    </button>
    <ConfirmDialog open={confirm} busy={busy} title={voice ? "Delete this voice note?" : "Delete this meeting?"}
      confirmLabel={voice ? "Delete voice note" : "Delete meeting"}
      consequence={<>
        Its transcript, notes and recording are deleted for good, on every device. Pages saved in a workspace stay.
        {waiting && <span role="status" className="vx-mc-note vx-mc-wait">Waiting for the recording to finish saving…</span>}
        {error && <span role="alert" className="vx-mc-note vx-mc-wait" data-tone="danger">{error}</span>}
      </>}
      onCancel={() => { if (!busy) { setConfirm(false); setError(""); } }}
      onConfirm={() => { if (!busy) void remove(); }} />
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
  // A VOICE NOTE (#1783) recording in this tab is stopped through the recorder, so the last words
  // are sent before the end; one recording elsewhere (another tab, a reload) is stopped like a bot.
  const vn = useVoiceNote();
  const isVoiceNote = m.platform === "voice_note";
  const thisNote = isVoiceNote && !!m.native_id && vn.nativeId === m.native_id;
  const recordingHere = thisNote && (vn.phase === "recording" || vn.phase === "stopping");
  // The note ended on the server (its bot left: Stop elsewhere, the idle rule, the cap) while this tab
  // still records it: let the microphone go and leave the recording state.
  const holding = thisNote && (vn.phase === "starting" || vn.phase === "recording");
  useEffect(() => { if (finished && holding && m.native_id) endVoiceNote(m.native_id); }, [finished, holding, m.native_id]);
  const audio = tracks.flatMap(t => (t.media_files || []).filter(f => f.type === "audio").map(f => ({ recording: t.id, media: f.id, format: f.format, origin: finiteDuration(f.capture_started_at_ms), duration: finiteDuration(f.duration_seconds) ?? finiteDuration(t.duration_seconds) })));
  // A Vexa call's transcriber ends with the call: there is nothing to stop by hand.
  const showStop = showBot && running.has(status) && !m.shared && !recordingHere && m.platform !== "vexa_call";
  const approximate = audio.some(t => !t.origin) ? "older recording timing is approximate" : "";
  return <section aria-label="Meeting controls" className="vx-mc" style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
    {recordingHere && <VoiceNoteRecorderBar />}
    {thisNote && vn.phase === "starting" && <span role="status" className="vx-mc-note">Connecting the microphone…</span>}
    {thisNote && vn.phase === "error" && vn.error && <p role="alert" className="vx-mc-note" data-tone="danger">{vn.error}</p>}
    {thisNote && recordingHere && vn.warning && <span role="status" className="vx-mc-note" data-tone="danger">{vn.warning}</span>}
    {showStop && <div><button className="vx-mc-btn" disabled={busy || !connected || status === "stopping"} onClick={() => void stopBot()}>{status === "stopping" ? "Stopping…" : isVoiceNote ? "Stop" : "Stop bot"}</button></div>}
    {deleted && <span role="status" className="vx-mc-note">Meeting audio, transcript and fixtures deleted. Saved pages are kept.</span>}
    {finished && !deleted && <>
      {loading ? <span role="status" className="vx-mc-note">Loading recording…</span>
        : !audio.length && !error ? <div style={{ display: "flex", alignItems: "center", gap: 6 }}><span className="vx-mc-note">No audio recording available.</span>{showDelete && <MeetingDeleteButton meetingId={m.id} />}</div> : null}
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
      {saveError && <div role="alert" className="vx-mc-note" data-tone="danger">{saveError}</div>}
    </>}
    {error && <div role="alert" className="vx-mc-note" data-tone="danger">{error} {finished && <button className="vx-mc-btn" onClick={() => setRetry(n => n + 1)}>Retry</button>}</div>}
  </section>;
}
