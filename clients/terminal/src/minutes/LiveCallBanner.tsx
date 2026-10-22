"use client";
/** A CHAT LISTENS TO A LIVE CALL (founder PRD, «A chat listening to a live call», 2026-10-22).
 *
 *  Every live call has exactly one chat it wakes — the attachment. At the top of a chat, for each live
 *  call the reader may read (the newest two):
 *    · elsewhere: "● Live call: <title> · listening in <chat> — Listen here · Not now". Listen here
 *      moves the attachment to this chat; Not now hides the line for this chat and this call.
 *    · here: "● Listening to <title>", what Vexa listens for (the meeting page's brief, editable), and
 *      for the meeting's owner the call's two opt-ins — speak in the call, post in the call's chat.
 *  A call the reader cannot read has no line. The state is re-read when the call's status changes and every
 *  few seconds while the call is live, so a move made from another chat shows up here. */
import "./liveCallBanner.css";
import { useCallback, useEffect, useState } from "react";
import type { MeetingMock } from "../surfaces/meetingModel";
import { useFeature } from "../platform/features";
import { registry } from "../contributions";
import { LIVE_CALL_BRIEF_KIND } from "../ui-kit/liveCallBriefSlot";
import { attachMeetingHere, readMeetingAttachment, setMeetingAttachmentOptIns, type MeetingAttachment } from "../data/meetingAttachment";
import { liveMeetings } from "./shell2/meetings";
import { callLabel } from "./callsList";
import "./minutes.css";

export const ATTACHMENT_POLL_MS = 10_000;
const NOT_NOW_KEY = (session: string, row: string) => `vx-live-call-not-now:${session}:${row}`;

function notNow(session: string, row: string): boolean {
  try { return localStorage.getItem(NOT_NOW_KEY(session, row)) === "1"; } catch { return false; }
}
function rememberNotNow(session: string, row: string): void {
  try { localStorage.setItem(NOT_NOW_KEY(session, row), "1"); } catch { /* locked-down storage: hidden for this view only */ }
}

const startedAt = (m: MeetingMock) => { const t = Date.parse(m.start_time || m.created_at || ""); return Number.isFinite(t) ? t : 0; };

/** The live calls a chat offers to listen to: live, not a voice note, newest first, at most two. */
export function liveCallsFor(meetings: readonly MeetingMock[]): MeetingMock[] {
  return liveMeetings(meetings).filter((m) => m.platform !== "voice_note")
    .sort((a, b) => startedAt(b) - startedAt(a)).slice(0, 2);
}

export function LiveCallBanners({ session, meetings }: { session: string; meetings: readonly MeetingMock[] }) {
  const calls = liveCallsFor(meetings);
  if (!session || session.startsWith("meet-") || !calls.length) return null;
  return <div className="vx-live-calls">{calls.map((m) => <LiveCallBanner key={`${session}:${m.id}`} session={session} meeting={m} />)}</div>;
}

export function LiveCallBanner({ session, meeting, pollMs = ATTACHMENT_POLL_MS }: { session: string; meeting: MeetingMock; pollMs?: number }) {
  const row = String(meeting.id);
  const [att, setAtt] = useState<MeetingAttachment | null | undefined>(undefined);
  const [hidden, setHidden] = useState(() => notNow(session, row));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const reread = useCallback(() => setNonce((n) => n + 1), []);
  const phase = meeting.live_status ?? "";

  useEffect(() => {
    let dead = false;
    readMeetingAttachment(row, session)
      .then((a) => { if (!dead) setAtt(a); })
      .catch(() => { /* a failed re-read keeps what is on screen; the next poll tries again */ });
    return () => { dead = true; };
  }, [row, session, nonce, phase]);
  useEffect(() => {
    if (!(pollMs > 0)) return;
    const t = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      reread();
    }, pollMs);
    return () => window.clearInterval(t);
  }, [pollMs, reread]);

  if (!att) return null;
  const title = callLabel(meeting);

  if (!att.attached_here) {
    if (hidden) return null;
    const where = att.attached ? (att.chat_title || "another chat") : "";
    const listenHere = async () => {
      setBusy(true); setError("");
      try { await attachMeetingHere(row, session); reread(); }
      catch { setError("Couldn't move listening here. Try again."); }
      finally { setBusy(false); }
    };
    return (
      <div data-live-call={row} data-attached="elsewhere" role="status" aria-live="polite" className="vx-live-call">
        <span className="vx-live-call-line">
          <span className="vx-live-chip-dot" aria-hidden />
          <span className="vx-live-call-text">Live call: {title}{where ? <> · listening in <span data-live-call-where="">{where}</span></> : null}</span>
        </span>
        <span className="vx-live-call-acts">
          {error && <span role="alert" className="vx-live-call-error">{error}</span>}
          <button type="button" data-live-call-listen="" className="vx-listen-btn vx-listen-btn-primary" disabled={busy}
            onClick={() => void listenHere()}>{busy ? "Moving…" : "Listen here"}</button>
          <button type="button" data-live-call-not-now="" className="vx-listen-btn" disabled={busy}
            onClick={() => { rememberNotNow(session, row); setHidden(true); }}>Not now</button>
        </span>
      </div>
    );
  }
  return <AttachedHere row={row} title={title} att={att} onChange={setAtt} />;
}

function AttachedHere({ row, title, att, onChange }: { row: string; title: string; att: MeetingAttachment; onChange: (a: MeetingAttachment) => void }) {
  const signals = useFeature("transcript_signals");
  const Brief = registry.tabComponent(LIVE_CALL_BRIEF_KIND);
  const [saving, setSaving] = useState<"" | "speak" | "post_chat">("");
  const [error, setError] = useState("");
  const toggle = async (key: "speak" | "post_chat", value: boolean) => {
    setSaving(key); setError("");
    try {
      const next = await setMeetingAttachmentOptIns(row, { [key]: value });
      onChange({ ...att, ...(next ? { speak: !!next.speak, post_chat: !!next.post_chat } : { [key]: value }) });
    } catch { setError("Couldn't save. Try again."); }
    finally { setSaving(""); }
  };
  return (
    <div data-live-call={row} data-attached="here" className="vx-live-call vx-live-call-here">
      <span role="status" aria-live="polite" className="vx-live-call-line">
        <span className="vx-live-chip-dot" aria-hidden />
        <span className="vx-live-call-text">Listening to {title}</span>
        {!signals && <span data-live-call-off="" className="vx-live-call-note">· listening is off for this account</span>}
      </span>
      {Brief && <Brief id={`live-call-brief:${row}`} params={{ meetingId: row, editable: att.owner }} active />}
      {att.owner && (
        <span data-live-call-optins="" className="vx-live-call-optins">
          <label className="vx-listen-wake">
            <input type="checkbox" data-live-call-speak="" checked={att.speak} disabled={!!saving}
              onChange={(e) => void toggle("speak", e.target.checked)} />
            May speak in the call
          </label>
          <label className="vx-listen-wake">
            <input type="checkbox" data-live-call-post="" checked={att.post_chat} disabled={!!saving}
              onChange={(e) => void toggle("post_chat", e.target.checked)} />
            May post in the call chat
          </label>
          {error && <span role="alert" className="vx-live-call-error">{error}</span>}
        </span>
      )}
    </div>
  );
}
