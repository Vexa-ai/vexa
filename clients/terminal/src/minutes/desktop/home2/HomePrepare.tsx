"use client";
/** PREPARE FROM HOME (feature `home_prepare`; founder 2026-10-18 on the felt prep panel: "why is that
 *  not in the home tab ready to start prep with chat and meeting from one click?"). The next planned
 *  meeting heads Home's Now strip:
 *    - within the hour: "<title> · in 30 min" with Prepare, and its readiness ticks;
 *    - later: "Fri 13:00 · <title>" with a quieter Prepare.
 *  (A meeting that is live already is the live-call row's — Join/Rejoin — not this one's.)
 *
 *  ONE CLICK on Prepare: the meeting opens on the right with its Prep, the meeting's conversation opens
 *  in the centre — the chat already about this meeting, or a new one in the meeting's workspace — and
 *  the composer is focused with the meeting's full reference as a badge (UI 7mm), never a bare title.
 *
 *  Read from the owners, nothing stored: the planned row from meeting-api (the meetings store), the
 *  ticks from the same reads as the Prep section (`minutes/prepRead.ts`). */
import { useEffect, useMemo, useState } from "react";
import { Circle, CircleCheck, Video } from "lucide-react";
import { ListRow, RowActions } from "../../../ui-kit";
import { ASK_CHAT_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT, WORKSPACE_COMMIT_EVENT } from "../../../platform/events";
import { useLiveMeetings } from "../../../surfaces/liveMeetings";
import type { MeetingMock } from "../../../surfaces/meetingModel";
import { readWorkspaceFile } from "../../../data/workspaceApi";
import { fetchMeetingNote } from "../../meetingNote";
import { prepPeople, readGoal, readMaterial, readiness, startsIn } from "../../prepRead";
import { usePeople } from "../../shell2/PeoplePage";
import { meNow } from "../../shell2/me";
import { CHAT_ABOUT_EVENT, aboutChats, holdPanel } from "../../shell2/about";
import "../../meetingPrep.css";

/** A meeting within this many ms is "soon": its row is strong and says "in N min". */
export const SOON_MS = 60 * 60_000;
/** How far ahead Home looks for the next planned meeting. */
export const AHEAD_MS = 7 * 86_400_000;
const PLANNED = new Set(["scheduled", "planned"]);
/** When the reference is offered to a resumed chat as it mounts (ms after it is opened). */
export const DRAFT_RETRY_MS = [120, 600, 1500] as const;
const fire = (name: string, detail?: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const startOf = (m: MeetingMock) => Date.parse(m.scheduled_at || "") || 0;

/** The next planned meeting from now, within the week, or null. */
export function nextPlanned(rows: readonly MeetingMock[], now: number = Date.now()): MeetingMock | null {
  const ahead = rows.filter((m) => PLANNED.has(m.live_status ?? "") && startOf(m) > now && startOf(m) - now <= AHEAD_MS);
  ahead.sort((a, b) => startOf(a) - startOf(b));
  return ahead[0] ?? null;
}

/** The meeting's full reference — what the composer draws as a badge (name · date and time). */
export const meetingRef = (id: string) => `[[meeting:${id}]]`;

/** THE ONE CLICK: the meeting (with its Prep) on the right, its conversation in the centre, the
 *  composer focused with the meeting referenced. */
export function prepareFromHome(m: Pick<MeetingMock, "id" | "title" | "workspace_id">): void {
  const id = String(m.id);
  fire(OPEN_MEETING_EVENT, { ref: id });
  window.setTimeout(() => {
    // the chat opens in the centre; the panel keeps the meeting it was just given
    holdPanel();
    const have = aboutChats(`meeting:${id}`)[0];
    if (have) {
      fire(OPEN_SESSION_EVENT, { session: have.session });
      // a draft for a chat that is not mounted yet is dropped (never stashed), so it is offered as the
      // chat comes in; the composer keeps a reference once, so a repeat changes nothing
      for (const ms of DRAFT_RETRY_MS) window.setTimeout(() => {
        fire(ASK_CHAT_EVENT, { mode: "draft", session: have.session, prompt: `${meetingRef(id)} ` });
        fire("vexa:terminal:focus-chat");
      }, ms);
    } else {
      fire(CHAT_ABOUT_EVENT, { key: `meeting:${id}`, path: `meeting:${id}`, label: m.title, kind: "meeting", slug: m.workspace_id, ref: meetingRef(id) });
    }
  }, 0);
}

/** The meeting page's text, read the way the meeting view reads it (where the server says it is), once
 *  and again after a commit — Home does not poll a note. "" when the meeting has no page yet. */
export function usePageBody(meetingId: string): string {
  const [body, setBody] = useState("");
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let dead = false;
    void (async () => {
      const note = await fetchMeetingNote(meetingId);
      if (!note.path) { if (!dead) setBody(""); return; }
      const text = note.readonly && typeof note.content === "string" ? note.content
        : await readWorkspaceFile(note.path, note.workspace ? { slug: note.workspace } : undefined).catch(() => null);
      if (!dead) setBody(text ?? "");
    })();
    return () => { dead = true; };
  }, [meetingId, nonce]);
  useEffect(() => {
    const on = () => setNonce((n) => n + 1);
    window.addEventListener(WORKSPACE_COMMIT_EVENT, on);
    return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, on);
  }, []);
  return body;
}

function Ticks({ m }: { m: MeetingMock }) {
  const body = usePageBody(String(m.id));
  const { people: known } = usePeople();
  const r = readiness(readGoal(body), useMemo(() => prepPeople(m.attendees, known, meNow()), [m.attendees, known]), readMaterial(body));
  const tick = (ok: boolean, l: string) => <span key={l} className="vx-prep-tick" data-prep-tick={l} data-ok={ok ? "" : undefined}>
    {ok ? <CircleCheck size={12} aria-hidden /> : <Circle size={12} aria-hidden />}{l}</span>;
  return <span className="vx-prep-ticks">{tick(r.goal, "goal")}{tick(r.people, "people")}{tick(r.material, "material")}</span>;
}

export function HomePrepare({ now }: { now?: number } = {}) {
  const rows = useLiveMeetings();
  const t = now ?? Date.now();
  const m = nextPlanned(rows, t);
  if (!m) return null;
  const at = startOf(m);
  const soon = at - t <= SOON_MS;
  const when = startsIn(at, t);
  return (
    <div className="vx-s2-need" data-home-prepare={soon ? "soon" : "later"} data-meeting={m.id}>
      <ListRow size="md" strong={soon} leading={<Video size={14} aria-hidden />}
        title={soon ? `${m.title} · ${when}` : `${when} · ${m.title}`} detail={<Ticks m={m} />}
        onSelect={() => prepareFromHome(m)}
        trailing={<RowActions verb={{ label: "Prepare", onClick: () => prepareFromHome(m) }} />} />
    </div>
  );
}
