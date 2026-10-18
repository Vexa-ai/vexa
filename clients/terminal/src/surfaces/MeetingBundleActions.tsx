"use client";
/** "Export" on a meeting and "Import meeting" on the meetings list (meeting-bundle.v1).
 *
 *  Export downloads the meeting as one file, with progress. Import takes a file (picker or drop),
 *  shows what it WOULD create — title, time, speakers, segments, recordings, anything this
 *  deployment will not restore — and imports only on Confirm. Everything a bundle carries is shown
 *  as text (React text nodes), never as markup. */
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Modal } from "../ui-kit/Modal";
import { refreshMeetings } from "./liveMeetings";
import type { MeetingMock } from "./meetingModel";
import {
  BundleError, confirmImport, downloadBundle, previewImport, restoreParts, saveBlob,
  type BundlePreview, type ImportResult, type PartsOutcome, type RestoreResult,
} from "./meetingBundle";
import "./meetingBundle.css";


const LIVE = new Set(["idle", "scheduled", "requested", "joining", "awaiting_admission", "needs_help", "active", "stopping"]);

/** The whole meeting is its owner's to export, once it has happened and ended (the server refuses a live
 *  one; a planned one has nothing to carry yet). */
export function exportable(m: Pick<MeetingMock, "shared" | "status" | "live_status">): boolean {
  return !m.shared && m.status !== "live" && !LIVE.has(m.live_status || "");
}

function describe(e: unknown): string {
  if (e instanceof BundleError) return e.code.startsWith("http_") ? e.message : `${e.message} (${e.code})`;
  return e instanceof Error ? e.message : "Request failed";
}

/** `autoStart`: the export was chosen from a menu — run it at once, and `onDone` after a clean
 *  finish so the menu's caller can take the button away again. */
export function ExportMeetingButton({ meetingId, autoStart = false, onDone }: { meetingId: string; autoStart?: boolean; onDone?: () => void }) {
  const [progress, setProgress] = useState<number | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [parts, setParts] = useState<PartsOutcome | null>(null);
  const busy = progress !== undefined;
  const run = async () => {
    setError(""); setParts(null); setProgress(null);
    try {
      const { blob, filename, parts: outcome } = await downloadBundle(meetingId, setProgress);
      saveBlob(blob, filename);
      setParts(outcome);
      if (onDone && outcome?.state !== "unavailable" && !(outcome?.state === "included" && outcome.skippedFiles > 0)) onDone();
    } catch (e) { setError(describe(e)); }
    finally { setProgress(undefined); }
  };
  const started = useRef(false);
  useEffect(() => { if (autoStart && !started.current) { started.current = true; void run(); } });
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
    <button className="bun-btn" disabled={busy} onClick={() => void run()} aria-label="Export meeting"
      title="Download this meeting as a file another Vexa deployment can import">
      {busy ? (progress == null ? "Exporting…" : `Exporting ${Math.round(progress * 100)}%`) : "Export"}
    </button>
    {error && <span role="alert" className="bun-small bun-danger">{error}</span>}
    {parts?.state === "unavailable" && <span role="status" className="bun-small bun-accent">
      Exported without the workspace and notes page: {parts.reason}</span>}
    {parts?.state === "included" && parts.skippedFiles > 0 && <span role="status" className="bun-small bun-accent">
      {parts.skippedFiles} workspace file{parts.skippedFiles === 1 ? "" : "s"} left out: name or size a bundle cannot carry</span>}
  </span>;
}

function when(iso: string | null): string {
  if (!iso) return "unknown time";
  const d = new Date(iso);
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : iso;
}

function PreviewCard({ p }: { p: BundlePreview }) {
  const mb = (n: number) => `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return <div data-bundle-preview className="bun-preview" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
    <div className="c-1 fw-600">{p.meeting.title || "Untitled meeting"}</div>
    <div>{p.meeting.platform} · {when(p.meeting.start_time)}</div>
    <div>{p.segments} transcript segment{p.segments === 1 ? "" : "s"}{p.speakers.length ? ` · ${p.speakers.join(", ")}` : ""}</div>
    <div>{p.media.length ? p.media.map(m => `${m.type} (${m.format}, ${mb(m.bytes)})`).join(" · ") : "No recordings — transcript only"}</div>
    {p.annotations.metadata_keys.length > 0 && <div>Annotations: {p.annotations.metadata_keys.join(", ")}</div>}
    {p.annotations.notes && <div>Notes</div>}
    {p.handoff.notes_page && <div>The meeting&rsquo;s page, into your personal workspace</div>}
    {p.handoff.workspace_files > 0 && <div>Workspace: {p.handoff.workspace_files} file{p.handoff.workspace_files === 1 ? "" : "s"}, as a new workspace of yours</div>}
    <div className="bun-note">Exported {when(p.exported_at)} from another deployment (meeting {p.source.meeting_id}). It will be yours only — nobody else gets access.</div>
    {p.duplicate_of != null && <div role="alert" className="bun-danger">
      You already imported this file as meeting {p.duplicate_of}. Delete that meeting to import it again.
    </div>}
  </div>;
}

export function ImportMeetingButton({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return <>
    <button className={compact ? "bun-btn" : "bun-btn mt-1_5"} data-compact={compact ? "" : undefined}
      onClick={() => setOpen(true)} aria-label="Import meeting" title="Import a meeting exported from a Vexa deployment">
      {compact ? "⇪" : "Import meeting"}
    </button>
    {open && <ImportDialog onClose={() => setOpen(false)} />}
  </>;
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);
  const [restored, setRestored] = useState<RestoreResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  // What is moving right now, and how much of it has gone: a 100 MB file is sent up to three times
  // (the preview, the import, the restore), and each is a visible phase, never a silent wait.
  const [phase, setPhase] = useState<{ label: string; fraction: number } | null>(null);
  const track = (label: string) => (fraction: number) => setPhase({ label, fraction });

  const choose = useCallback(async (f: File | undefined) => {
    if (!f) return;
    setFile(f); setPreview(null); setDone(null); setError(""); setBusy(true);
    try { setPreview(await previewImport(f, fetch, track("Checking the file"))); }
    catch (e) { setError(describe(e)); }
    finally { setBusy(false); setPhase(null); }
  }, []);
  const confirm = async () => {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const result = await confirmImport(file, fetch, track("Importing"));
      setDone(result); refreshMeetings();
      // The meeting has landed; the agent domain now lands what it owns from the same file. A
      // refusal there is shown — the meeting stays imported either way.
      if (result.handoff.workspace_files > 0 || result.handoff.notes_page) {
        try { setRestored(await restoreParts(file, result.meeting_id, fetch, track("Restoring the workspace and page"))); }
        catch (e) { setError(`The meeting was imported, but its workspace and notes page were not: ${describe(e)}`); }
      }
    }
    catch (e) { setError(describe(e)); }
    finally { setBusy(false); setPhase(null); }
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); void choose(e.dataTransfer.files?.[0]); };

  return <Modal title="Import meeting" width={460} onClose={() => { if (!busy) onClose(); }}>
    {done ? <div role="status" className="bun-done">
      Imported as meeting {done.meeting_id}: {done.meeting.title || "Untitled meeting"}, {done.segments} segments
      {done.media.length ? `, ${done.media.length} recording${done.media.length === 1 ? "" : "s"}` : ""}.
      {restored?.workspace && <div>Workspace restored: {restored.workspace.files} files in &ldquo;{restored.workspace.slug}&rdquo;.</div>}
      {restored?.notes_page && <div>{restored.notes_page.written ? "The meeting\u2019s page is in your personal workspace." : "Your personal workspace already had this meeting\u2019s page; it was kept."}</div>}
      {error && <div role="alert" className="mt-2 bun-small bun-danger">{error}</div>}
      {busy && phase && <div role="status" className="bun-note mt-2">{phase.label}… {Math.round(phase.fraction * 100)}%
        <progress value={phase.fraction} max={1} className="mt-1_5" style={{ display: "block", width: "100%" }} /></div>}
      <div className="bun-mt-14" style={{ display: "flex", justifyContent: "flex-end" }}><button className="bun-btn" onClick={onClose}>Done</button></div>
    </div> : <>
      <label data-bundle-drop onDragOver={e => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}
        className="bun-drop bun-note" data-over={over ? "" : undefined} style={{ display: "block", textAlign: "center", cursor: "pointer" }}>
        {file ? file.name : "Drop a .meeting-bundle.zip here, or click to choose one"}
        <input type="file" accept=".zip,application/zip" aria-label="Meeting bundle file" style={{ display: "none" }}
          onChange={e => void choose(e.target.files?.[0])} />
      </label>
      {busy && <div role="status" className="bun-note bun-mt-10">
        {phase ? `${phase.label}… ${Math.round(phase.fraction * 100)}%` : preview ? "Importing…" : "Checking the file…"}
        {phase && <progress value={phase.fraction} max={1} className="mt-1_5" style={{ display: "block", width: "100%" }} />}
      </div>}
      {preview && <div className="mt-3"><PreviewCard p={preview} /></div>}
      {error && <div role="alert" className="bun-mt-10 bun-small bun-danger">{error}</div>}
      <div className="mt-4" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="bun-btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button className="bun-btn" data-variant="primary" style={{ opacity: !preview || busy || preview.duplicate_of != null ? 0.45 : 1 }}
          disabled={!preview || busy || preview.duplicate_of != null} onClick={() => void confirm()}>Import</button>
      </div>
    </>}
  </Modal>;
}
