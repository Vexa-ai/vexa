"use client";
/** SCHEDULE A CALL (R1 2.4, `rooms`; ADR "Planned meetings", founder 2026-10-15). A room plans a call:
 *  a title, a time, how long, who to invite and an optional agenda page. The room owns the planned
 *  meeting (`POST /api/rooms/{s}/plan`, 1.4). NOTHING is sent from here: the creator adds the call to
 *  their own calendar — the `.ics`, or the invite copied — on their own click, so invitations come
 *  from them. Room members see it in the room either way. */
import { useEffect, useState } from "react";
import { listWorkspaceMembers } from "../../data/workspaceApi";
import { CalendarClock } from "lucide-react";
import { Modal } from "../../ui-kit/Modal";
import { planRoomCall, recordRoomCallEvent, type PlannedCall } from "../../surfaces/roomsApi";
import { useFeature } from "../../platform/features";
import { refUrl } from "../../ui-kit/universalRefs";
import "./rooms.css";

const LENGTHS = [15, 30, 45, 60] as const;
const EMAIL = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

/** `YYYY-MM-DDTHH:mm` in local time — the next whole hour, at least 30 minutes away. */
export function nextSlot(now: Date = new Date()): string {
  const d = new Date(now.getTime() + 30 * 60_000);
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** The addresses typed into the invite field (commas, semicolons or spaces), and the ones that are not. */
export function parseInvitees(text: string): { ok: string[]; bad: string[] } {
  const all = text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  const ok = [...new Set(all.filter((a) => EMAIL.test(a)).map((a) => a.toLowerCase()))];
  return { ok, bad: all.filter((a) => !EMAIL.test(a)) };
}

const when = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
};

const refusal = (e: unknown): string => {
  const s = JSON.stringify((e as { body?: unknown })?.body ?? (e as { message?: string })?.message ?? "");
  if (/meetings_not_deployed/.test(s)) return "Planned calls are not available here yet.";
  if (/not_a_workspace_writer|session_read_only/.test(s)) return "You can't plan calls in this room.";
  if (/feature_disabled/.test(s)) return "Rooms are not switched on here.";
  return "Could not plan the call. Try again.";
};

export function ScheduleCallDialog({ session, label, workspace, workspaceName, onClose, onPlanned, now }: {
  session: string; label: string; onClose: () => void;
  /** the room's workspace: named in the header, and its members prefill the invite (JTBD-1) */
  workspace?: string; workspaceName?: string;
  onPlanned?: (p: PlannedCall) => void;
  /** for tests: the clock the default time is taken from */
  now?: Date;
}) {
  const [title, setTitle] = useState(label);
  const [at, setAt] = useState(() => nextSlot(now));
  const [mins, setMins] = useState<number>(30);
  const [who, setWho] = useState("");
  const [agenda, setAgenda] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState<PlannedCall | null>(null);
  const [copied, setCopied] = useState(false);
  // SEND FROM MY CALENDAR (PLAN-8H 8.4): the creator's own click creates the event in THEIR calendar
  // (the existing `calendar_write` route, as them) and records it as the call's source of truth.
  const calendarOn = useFeature("calendar_write");
  const [sent, setSent] = useState<"idle" | "sending" | "sent">("idle");
  const invitees = parseInvitees(who);
  // WHERE IT IS MADE, AND FOR WHOM (founder 2026-10-16): the invite starts with every member of the
  // room's workspace who has an address; the creator trims it before anything is planned or sent.
  useEffect(() => {
    if (!workspace) return;
    let on = true;
    void listWorkspaceMembers(workspace).then((ms) => {
      const emails = [...new Set(ms.map((m) => (m.email ?? "").trim().toLowerCase()).filter((e) => EMAIL.test(e)))];
      if (on && emails.length) setWho((cur) => cur.trim() ? cur : emails.join(", "));
    }).catch(() => undefined);
    return () => { on = false; };
  }, [workspace]);

  const plan = async () => {
    setErr("");
    const start = new Date(at);
    if (!title.trim()) return setErr("Give the call a title.");
    if (Number.isNaN(start.getTime())) return setErr("Pick a time.");
    if (invitees.bad.length) return setErr(`Not an email address: ${invitees.bad.join(", ")}`);
    setBusy(true);
    try {
      const r = await planRoomCall(session, {
        title: title.trim(), start: start.toISOString(), end: new Date(start.getTime() + mins * 60_000).toISOString(),
        invitees: invitees.ok, agenda: agenda.trim() || undefined,
      });
      setDone(r); onPlanned?.(r);
    } catch (e) { setErr(refusal(e)); }
    setBusy(false);
  };

  const copy = async () => {
    if (!done) return;
    const d = done.calendar_draft;
    const text = [d.summary, when(d.start), d.attendees.length ? `With: ${d.attendees.join(", ")}` : "", d.description].filter(Boolean).join("\n");
    try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setErr("Could not copy. Download the .ics instead."); }
  };

  const sendFromCalendar = async () => {
    if (!done) return;
    const d = done.calendar_draft;
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const data = (done.meeting.data ?? {}) as { workspace_id?: string; agenda?: string };
    const lines = [`Join the call in Vexa: ${refUrl("chat", session, origin)}`];
    if (data.agenda && data.workspace_id) lines.push(`Agenda: ${origin}/w/${encodeURIComponent(data.workspace_id)}/${data.agenda.split("/").map(encodeURIComponent).join("/")}`);
    else if (data.agenda) lines.push(`Agenda: ${data.agenda}`);
    setErr(""); setSent("sending");
    try {
      const r = await fetch("/api/connections/calendar/event/create", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          request_id: `room-${done.meeting.id}-${Date.now().toString(36)}`,
          title: d.summary, start: { date_time: d.start }, end: { date_time: d.end ?? d.start },
          description: lines.join("\n"), attendees: d.attendees, notify: true,
        }),
      });
      const body = await r.json().catch(() => ({})) as { status?: string; event_id?: string; detail?: unknown };
      if (!r.ok || body.status !== "event_created" || !body.event_id) {
        const det = body.detail as { message?: string; policy_key?: string } | string | undefined;
        const why = typeof det === "string" ? det : det?.message ?? (det?.policy_key ? `your organisation's ${det.policy_key} setting` : "");
        setSent("idle");
        return setErr(`Not sent from your calendar${why ? ` — ${why}` : ""}. Download the .ics or copy the invite instead.`);
      }
      await recordRoomCallEvent(session, done.meeting.id, body.event_id);
      setSent("sent");
    } catch { setSent("idle"); setErr("Not sent from your calendar. Download the .ics or copy the invite instead."); }
  };

  if (done) {
    const d = done.calendar_draft;
    return (
      <Modal title="Call planned" onClose={onClose} width={420}>
        <div className="vx-rooms-form" data-schedule-call="planned">
          <p className="vx-rooms-planned"><CalendarClock size={16} aria-hidden /><span><b>{d.summary}</b><small>{when(d.start)}{d.attendees.length ? ` · ${d.attendees.length} invited` : ""}</small></span></p>
          <p className="vx-rooms-note">{sent === "sent"
            ? "Sent from your calendar. The invitations come from you; changes made in your calendar update this call."
            : "Nothing has been sent. Add it to your calendar to send the invitations from you."}</p>
          {err && <p className="vx-rooms-error" role="alert">{err}</p>}
          <div className="vx-rooms-acts">
            {calendarOn && sent !== "sent" && (
              <button type="button" className="vx-btn" data-variant="primary" disabled={sent === "sending"}
                onClick={() => void sendFromCalendar()}>Send from my calendar</button>)}
            <button type="button" className="vx-btn" data-variant="secondary" onClick={() => void copy()}>{copied ? "Copied" : "Copy invite"}</button>
            <a className="vx-btn" data-variant="secondary" href={`/api${done.ics}`} download>Download .ics</a>
            <button type="button" className="vx-btn" data-variant={calendarOn && sent !== "sent" ? "secondary" : "primary"} onClick={onClose}>Done</button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Schedule a call" onClose={onClose} width={420}>
      <div className="vx-rooms-form" data-schedule-call="">
        <p className="vx-rooms-note" data-schedule-where="">{`In ${label}${workspaceName ? ` · ${workspaceName}` : ""}`}</p>
        <label className="vx-rooms-field"><span>Title</span>
          <input className="vx-rooms-input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} /></label>
        <label className="vx-rooms-field"><span>When</span>
          <input className="vx-rooms-input" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></label>
        <div className="vx-rooms-field"><span>How long</span>
          <div className="vx-rooms-seg" role="radiogroup" aria-label="How long">
            {LENGTHS.map((m) => <button key={m} type="button" role="radio" aria-checked={mins === m} onClick={() => setMins(m)}>{m} min</button>)}
          </div></div>
        <label className="vx-rooms-field"><span>Invite by email</span>
          <input className="vx-rooms-input" value={who} onChange={(e) => setWho(e.target.value)} placeholder="name@example.com, …" /></label>
        <p className="vx-rooms-note">The room's members see the call here. Guests join the call, not the room.</p>
        <label className="vx-rooms-field"><span>Agenda page (optional)</span>
          <input className="vx-rooms-input" value={agenda} onChange={(e) => setAgenda(e.target.value)} placeholder="e.g. meetings/launch-agenda.md" maxLength={512} /></label>
        {err && <p className="vx-rooms-error" role="alert">{err}</p>}
        <div className="vx-rooms-acts">
          <button type="button" className="vx-btn" data-variant="secondary" onClick={onClose}>Cancel</button>
          <button type="button" className="vx-btn" data-variant="primary" disabled={busy} onClick={() => void plan()}>Plan call</button>
        </div>
      </div>
    </Modal>
  );
}
