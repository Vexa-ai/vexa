"use client";
/** The meeting / voice-note page's ONE actions area (#1783): the state's two or three primary acts as
 *  buttons, Export where the meeting can be exported, and the rest behind "…" (`meetingActs`). Acts
 *  whose control already lives in the header — the title's rename, the live language picker, Share,
 *  Export — are reached from the menu by pressing that control, so there is one implementation of
 *  each. The rail's meeting rows open the page and send the same act here (`MEETING_ACT_EVENT`). */
import { useEffect, useRef, useState, type RefObject } from "react";
import { MoreHorizontal } from "lucide-react";
import type { MeetingMock } from "../surfaces/meetingModel";
import { refreshMeetings } from "../surfaces/liveMeetings";
import { ContextMenu } from "../ui-kit/ContextMenu";
import { ConfirmDialog, IconButton } from "../ui-kit";
import { ExportMeetingButton, exportable } from "../surfaces/MeetingBundleActions";
import { createMeetingNotes, updateMeetingNotes } from "./meetingNote";
import { pauseVoiceNote, stopVoiceNote, useVoiceNote } from "./voiceNote";
import { deleteMeetingData, useMeetingDeletedHere } from "./meetingDeletion";
import { meetingActs, sendBotAgain, stopBot, type MeetingActId } from "./meetingActions";
import "./minutes.css";

/** `window` event `{ meetingId, act }`: perform a header act on that meeting's page (the rail sends
 *  it after opening the page, for the acts whose control is on the page). */
export const MEETING_ACT_EVENT = "vexa:meeting-act";

/** Ask the meeting's page to perform `act`, retrying while the page mounts (the rail opens it first).
 *  Resolves true once a page took it. */
export async function sendMeetingAct(meetingId: string, act: MeetingActId, tries = 12, everyMs = 250): Promise<boolean> {
  for (let i = 0; i < tries; i++) {
    const detail = { meetingId, act, handled: false };
    window.dispatchEvent(new CustomEvent(MEETING_ACT_EVENT, { detail }));
    if (detail.handled) return true;
    await new Promise((r) => setTimeout(r, everyMs));
  }
  return false;
}

export function MeetingActionsBar({ meeting: m, header }: { meeting: MeetingMock; header: RefObject<HTMLElement | null> }) {
  const vn = useVoiceNote();
  const deletedHere = useMeetingDeletedHere(m.id);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [busy, setBusy] = useState<MeetingActId | null>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const recording = m.platform === "voice_note" && vn.meetingId === m.id && (vn.phase === "recording" || vn.phase === "starting")
    ? { paused: !!vn.paused } : null;
  const press = (sel: string) => (header.current?.querySelector(sel) as HTMLElement | null)?.click();
  const canExport = exportable(m);
  const acts = deletedHere ? [] : meetingActs(m, {
    recording,
    canRename: !m.shared,
    canShare: true,
    canExport: false,            // Export is its own button here (it shows its own progress)
    canChangeLanguage: true,
  });
  const primary = acts.filter((a) => a.primary);
  const rest = acts.filter((a) => !a.primary);

  async function run(id: MeetingActId) {
    setError("");
    if (id === "rename") { press("button[data-call-name]"); return; }
    if (id === "share") { press('button[title="Share this meeting"]'); return; }
    if (id === "export") { press('[aria-label="Export meeting"]'); return; }
    if (id === "language") { (header.current?.querySelector("[data-meeting-language]")?.parentElement?.querySelector("button") as HTMLElement | null)?.click(); return; }
    if (id === "delete") { setDeleting(true); return; }
    if (id === "pause") { pauseVoiceNote(true); return; }
    if (id === "resume") { pauseVoiceNote(false); return; }
    if (id === "stop-note") { void stopVoiceNote(); return; }
    setBusy(id);
    try {
      if (id === "stop-bot") await stopBot(m);
      if (id === "send-again") await sendBotAgain(m);
      if (id === "notes") { const path = await createMeetingNotes(m.id); updateMeetingNotes(m.id, path); }
      refreshMeetings();
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't do that. Try again."); }
    finally { setBusy(null); }
  }
  const live = useRef(run);
  live.current = run;
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ meetingId?: string; act?: MeetingActId; handled?: boolean }>).detail;
      if (d && d.act && !d.handled && (d.meetingId === m.id || d.meetingId === m.native_id)) { d.handled = true; void live.current(d.act); }
    };
    window.addEventListener(MEETING_ACT_EVENT, on);
    return () => window.removeEventListener(MEETING_ACT_EVENT, on);
  }, [m.id, m.native_id]);

  async function remove() {
    setBusy("delete"); setError(""); setWaiting(false);
    try { await deleteMeetingData(m.id, { onWaiting: () => setWaiting(true), native: m.native_id }); setDeleting(false); refreshMeetings(); }
    catch (e) { setError(e instanceof Error ? e.message : "Couldn't delete. Try again."); }
    finally { setBusy(null); setWaiting(false); }
  }
  const voice = m.platform === "voice_note";
  if (deletedHere) return null;
  return <div data-meeting-actions className="vx-meeting-acts" role="toolbar" aria-label={voice ? "Voice note actions" : "Meeting actions"}>
    {primary.map((a) => (
      <button key={a.id} type="button" className="bun-btn" data-meeting-act={a.id} disabled={!!busy} aria-busy={busy === a.id || undefined}
        onClick={() => void run(a.id)}>{busy === a.id ? `${a.label}…` : a.label}</button>
    ))}
    {canExport && <ExportMeetingButton meetingId={m.id} />}
    {rest.length > 0 && <IconButton label={voice ? "More voice note actions" : "More meeting actions"} size="xs" aria-haspopup="menu" aria-expanded={!!menu}
      onClick={(e) => { if (menu) { setMenu(null); return; } const b = e.currentTarget.getBoundingClientRect(); setMenu({ x: b.right - 188, y: b.bottom + 4 }); }}>
      <MoreHorizontal size={14} strokeWidth={1.75} />
    </IconButton>}
    {menu && <ContextMenu x={menu.x} y={menu.y} label={voice ? "Voice note actions" : "Meeting actions"} onClose={() => setMenu(null)}
      items={rest.map((a) => ({ id: a.id, label: a.label, danger: a.danger, onSelect: () => { setMenu(null); void run(a.id); } }))} />}
    {error && <span role="alert" className="vx-mc-note" data-tone="danger">{error}</span>}
    <ConfirmDialog open={deleting} busy={busy === "delete"} title={voice ? "Delete this voice note?" : "Delete this meeting?"}
      confirmLabel={voice ? "Delete voice note" : "Delete meeting"} focusConfirm
      consequence={<>
        Its transcript, notes and recording are deleted for good, on every device. Pages saved in a workspace stay.
        {waiting && <span role="status" className="vx-mc-note vx-mc-wait">Waiting for the recording to finish saving…</span>}
      </>}
      onCancel={() => { if (busy !== "delete") setDeleting(false); }}
      onConfirm={() => { if (busy !== "delete") void remove(); }} />
  </div>;
}
