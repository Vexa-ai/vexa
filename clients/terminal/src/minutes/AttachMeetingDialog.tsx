"use client";
/** "USE AS MEETING PAGE" — a page made the meeting's page, from the page's "…" (#1783).
 *
 *  Founder, 2026-10-15: *"why not map page to meeting reliably?"* An agenda written before a call is
 *  the meeting's page only if it is bound to it. The person picks the meeting; agent-api stamps the
 *  page with the meeting's identity and records it on the meeting (`POST /api/meeting/note/attach`,
 *  the same door the agent's `meeting_attach_page` uses), so the sidebar, Update notes and the
 *  post-meeting report all use this page. A meeting that already has a page with words in it is
 *  refused with that page's name; the person merges, or replaces on purpose. */
import "./attachMeetingDialog.css";
import { useMemo, useState } from "react";
import { Modal } from "../ui-kit/Modal";
import { Button } from "../ui-kit";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { meetingDisplayName } from "./callsList";
import type { MeetingMock } from "../surfaces/meetingModel";

export type AttachAnswer =
  | { ok: true; path: string; workspace: string }
  | { ok: false; conflict?: { path: string; workspace: string }; message: string };

/** The one call: bind `path` (in `workspace`, "" = Personal) to meeting `meetingId`. */
export async function attachMeetingPage(path: string, workspace: string, meetingId: string, replace = false,
                                        fetcher: typeof fetch = fetch): Promise<AttachAnswer> {
  try {
    const r = await fetcher("/api/meeting/note/attach", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, workspace, meeting_id: meetingId, ...(replace ? { replace: true } : {}) }),
    });
    const body = await r.json().catch(() => ({})) as { path?: string; workspace?: string; detail?: unknown };
    if (r.ok) return { ok: true, path: String(body.path ?? path), workspace: String(body.workspace ?? workspace) };
    const d = (body.detail ?? {}) as { said?: string; current?: { path?: string; workspace?: string } } | string;
    if (typeof d === "object" && d.current) {
      return { ok: false, conflict: { path: String(d.current.path ?? ""), workspace: String(d.current.workspace ?? "") },
        message: d.said || "This meeting already has a page." };
    }
    return { ok: false, message: typeof d === "string" ? d : d.said || (r.status === 403 ? "You can't use this meeting." : "Could not do that. Retry.") };
  } catch {
    return { ok: false, message: "Could not do that. Retry." };
  }
}

const when = (m: MeetingMock) => {
  const t = Date.parse(String(m.start_time || m.scheduled_at || m.created_at || ""));
  return Number.isFinite(t) ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(t)) : "";
};

export function AttachMeetingDialog({ path, workspace, onClose, onAttached }: {
  path: string; workspace: string; onClose: () => void; onAttached?: (meetingId: string) => void;
}) {
  const meetings = useLiveMeetings();
  const rows = useMemo(() => [...meetings]
    .filter((m) => !m.artifacts_deleted)
    .sort((a, b) => (Date.parse(String(b.start_time || b.scheduled_at || b.created_at || "")) || 0)
      - (Date.parse(String(a.start_time || a.scheduled_at || a.created_at || "")) || 0))
    .slice(0, 12), [meetings]);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<AttachAnswer | null>(null);
  const go = async (replace = false) => {
    if (!picked) return;
    setBusy(true);
    const a = await attachMeetingPage(path, workspace, picked, replace);
    setBusy(false); setAnswer(a);
    if (a.ok) { onAttached?.(picked); onClose(); }
  };
  const conflict = answer && !answer.ok ? answer.conflict : undefined;
  return (
    <Modal title="Use as meeting page" onClose={onClose} width={440}>
      <div data-attach-meeting className="vx-am">
        <p className="vx-am-lede">This page becomes the meeting's page: the one its sidebar shows, Update notes writes and the report lands in.</p>
        {rows.length === 0 && <p className="vx-am-empty">No meetings yet.</p>}
        <div role="radiogroup" aria-label="Meeting" className="vx-am-list">
          {rows.map((m) => (
            <label key={m.id} data-attach-meeting-row={m.id} className="vx-am-row">
              <input type="radio" name="attach-meeting" value={m.id} checked={picked === String(m.id)}
                onChange={() => { setPicked(String(m.id)); setAnswer(null); }} />
              <span className="vx-am-name">{meetingDisplayName(m)}</span>
              <span className="vx-am-when">{when(m)}</span>
            </label>
          ))}
        </div>
        {answer && !answer.ok && <p role="alert" data-attach-meeting-error className="vx-am-error">{answer.message}</p>}
        <div className="vx-am-acts">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {conflict
            ? <Button data-attach-meeting-replace variant="primary" disabled={busy} onClick={() => void go(true)}>Use this page instead</Button>
            : <Button data-attach-meeting-go variant="primary" disabled={!picked || busy} onClick={() => void go(false)}>{busy ? "Saving…" : "Use as meeting page"}</Button>}
        </div>
      </div>
    </Modal>
  );
}
