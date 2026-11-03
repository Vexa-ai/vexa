"use client";
/** START A MEETING — THREE CHOICES (founder validation O1, felt on the lab mock 2026-10-18).
 *
 *  1. **Call a person** — the people you share a workspace with (per viewer, `GET /api/people/mentionable`).
 *     Call rings them in your room of two (`POST /api/rooms/call`), the same act the agent's
 *     `call_person` runs; a call already live with them asks "Call with Megan is still running. Rejoin
 *     it?" first.
 *  2. **Start in a room** — your rooms; a room with a live call says Rejoin.
 *  3. **Schedule for later** — the room's own Schedule a call: nothing reaches a calendar until you add
 *     it yourself. Without rooms it says "coming".
 *
 *  A Vexa call is the default; Google Meet, Teams and Zoom sit under "Other options" — Vexa joins
 *  those from their link (Send Vexa to a meeting). Nothing here invents a primitive: a call is a
 *  Meeting held in a Session (room), started by the Person. */
import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Hash, Phone } from "lucide-react";
import { AvatarStack, Button, Input, ListRow, Menu, RowCell, SectionLabel } from "../../ui-kit";
import { listMentionable, type MentionablePerson } from "../../surfaces/sessionsApi";
import { listRooms, type RoomListing } from "../../surfaces/roomsApi";
import { getCall } from "../../surfaces/callsApi";
import { openRoomOfTwo } from "../../surfaces/roomPeopleApi";
import { useFeature } from "../../platform/features";
import { CALL_PLACED_EVENT, OPEN_SESSION_EVENT, START_MEETING_EVENT } from "../../platform/events";
import { useDesktopState } from "../desktop/desktopApi";
import { ScheduleCallDialog } from "../rooms/ScheduleCallDialog";
import { askRejoin, closeRejoin } from "../callRejoin";
import { enterCall } from "../callState";
import { personCallName, roomCallName } from "./callNames";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

type RoomRow = RoomListing & { workspace: string; workspaceName: string };

/** The answer of `POST /api/rooms/call` the chooser reads. */
interface CallAnswer { session_id: string; already_live?: boolean; with?: { name?: string } | null; call?: { room?: string } }

async function postCall(body: Record<string, unknown>): Promise<CallAnswer> {
  const r = await fetch("/api/rooms/call", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (j as { detail?: { said?: string; refused?: string } | string }).detail;
    throw new Error(typeof d === "object" && d?.said ? d.said : "Couldn't start the call. Try again.");
  }
  return j as CallAnswer;
}

/** A started (or rejoined) call with a person: the shell opens the room and joins as the caller. */
const placed = (a: CallAnswer, name: string) =>
  fire(CALL_PLACED_EVENT, { session: a.session_id, room: a.call?.room ?? "", with: a.with?.name || name, alreadyLive: !!a.already_live });

/** CALL A PERSON without a duplicate (founder O1): a call already live in your room of two with them
 *  is asked about first — Rejoin joins it; Start a new one ends it (only when nobody else is in it)
 *  and starts another. Exported for the tests. */
export const liveCallPersonDeps = {
  openRoom: openRoomOfTwo as (subject: string) => Promise<{ session_id: string }>,
  getCall: getCall as (session: string) => Promise<{ call: unknown }>,
  post: postCall, ask: askRejoin, placed,
  open: (s: string) => fire(OPEN_SESSION_EVENT, { session: s }),
  join: (s: string, label: string): Promise<unknown> => enterCall(s, label),
  fail: (_msg: string): void => undefined,
};
export async function callPerson(p: { subject: string; name: string }, deps: typeof liveCallPersonDeps = liveCallPersonDeps): Promise<"rang" | "asked"> {
  const label = personCallName(p.name);
  const room = await deps.openRoom(p.subject);
  const sid = room.session_id;
  const state = await deps.getCall(sid).catch(() => null);
  if (state?.call) {
    deps.ask({
      label,
      onRejoin: () => { closeRejoin(); deps.open(sid); void deps.join(sid, label).catch(() => undefined); },
      onNew: () => { closeRejoin(); void deps.post({ room: sid, fresh: true }).then((a) => deps.placed(a, p.name)).catch((e) => deps.fail((e as Error).message)); },
    });
    return "asked";
  }
  deps.placed(await deps.post({ person: p.subject }), p.name);
  return "rang";
}

export function StartMeeting({ onClose, onOtherPlatform }: { onClose: () => void; onOtherPlatform: () => void }) {
  const roomsOn = useFeature("rooms");
  const callsOn = useFeature("calls");
  const { desktop } = useDesktopState();
  const [people, setPeople] = useState<MentionablePerson[] | null>(null);
  const [q, setQ] = useState("");
  const [rooms, setRooms] = useState<RoomRow[] | null>(null);
  const [live, setLive] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [schedule, setSchedule] = useState<RoomRow | null>(null);

  useEffect(() => {
    let on = true;
    void listMentionable().then((m) => { if (on) setPeople(m.people.filter((p) => p.subject)); }).catch(() => { if (on) setPeople([]); });
    return () => { on = false; };
  }, []);
  const workspaces = useMemo(() => (desktop?.sections.find((s) => s.id === "workspaces")?.items ?? [])
    .filter((i) => i.kind === "workspace")
    .map((i) => ({ slug: String((i as { slug?: string }).slug || ""), name: String((i as { name?: string }).name || "") }))
    .filter((w) => w.slug && w.slug !== "personal").slice(0, 10), [desktop]);
  useEffect(() => {
    if (!roomsOn) { setRooms([]); return; }
    let on = true;
    void Promise.all(workspaces.map((w) => listRooms(w.slug).then((rs) => rs.filter((r) => r.member).map((r) => ({ ...r, workspace: w.slug, workspaceName: w.name }))).catch(() => [] as RoomRow[])))
      .then((all) => {
        if (!on) return;
        const rows = all.flat().sort((a, b) => (b.last_active ?? 0) - (a.last_active ?? 0)).slice(0, 12);
        setRooms(rows);
        // a room with a live call says Rejoin (read once per open of the chooser)
        void Promise.all(rows.map((r) => getCall(r.id).then((c) => [r.id, !!c.call] as const).catch(() => [r.id, false] as const)))
          .then((pairs) => { if (on) setLive(Object.fromEntries(pairs)); });
      });
    return () => { on = false; };
  }, [roomsOn, workspaces]);

  const shown = (people ?? []).filter((p) => !q.trim() || p.name.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8);
  const canCall = roomsOn && callsOn;
  const call = async (p: MentionablePerson) => {
    setBusy(p.subject); setErr("");
    try { await callPerson({ subject: p.subject, name: p.name || p.subject }, { ...liveCallPersonDeps, fail: setErr }); onClose(); }
    catch (e) { setErr((e as Error).message || "Couldn't start the call. Try again."); }
    setBusy("");
  };
  const startRoom = (r: RoomRow) => { onClose(); fire(START_MEETING_EVENT, { room: r.id, label: roomCallName(r.title || "") }); };

  return (
    <section className="vx-s2-chooser" data-start-chooser="" aria-label="Start a meeting">
      <div className="vx-s2-choice" data-choice="person">
        <SectionLabel as="h3">Call a person</SectionLabel>
        <p className="vx-s2-note">{canCall ? "Rings them in your room of two. A Vexa call; no link to send." : "Calls are coming here."}</p>
        {canCall && <>
          <Input label="Find a person" hideLabel control="sm" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Someone you share a workspace with" />
          <div className="vx-s2-choice-list">
            {people === null ? <p className="vx-s2-note">Loading…</p> : shown.length === 0 ? <p className="vx-s2-note">Nobody shares a workspace with you yet.</p>
              : shown.map((p) => <ListRow key={p.subject} size="md" strong title={p.name || p.subject} detail={p.workspaces?.[0]}
                leading={<AvatarStack names={[p.name || p.subject]} max={1} />}
                trailing={<RowCell w={104} align="end"><Button variant="ghost" icon={<Phone size={14} />} loading={busy === p.subject} data-call-person={p.subject} onClick={() => void call(p)}>Call</Button></RowCell>} />)}
          </div>
        </>}
        {err && <p className="vx-s2-note" role="alert">{err}</p>}
      </div>
      <div className="vx-s2-choice" data-choice="room">
        <SectionLabel as="h3">Start in a room</SectionLabel>
        <p className="vx-s2-note">Everyone in the room can join from there.</p>
        <div className="vx-s2-choice-list">
          {!roomsOn ? <p className="vx-s2-note">Rooms are coming here.</p> : rooms === null ? <p className="vx-s2-note">Loading…</p>
            : rooms.length === 0 ? <p className="vx-s2-note">No rooms yet.</p>
            : rooms.map((r) => <ListRow key={r.id} size="md" strong leading={<Hash size={14} />} title={r.title || "Room"} detail={r.workspaceName || r.workspace}
              trailing={<RowCell w={104} align="end"><Button variant="ghost" data-start-room={r.id} onClick={() => startRoom(r)}>{live[r.id] ? "Rejoin" : "Start"}</Button></RowCell>} />)}
        </div>
      </div>
      <div className="vx-s2-choice" data-choice="schedule">
        <SectionLabel as="h3">Schedule for later</SectionLabel>
        {roomsOn && rooms && rooms.length > 0 ? <>
          <p className="vx-s2-note">Plan it in a room. Nothing goes to a calendar until you add it yourself.</p>
          <Menu label="Schedule in a room" align="start" trigger={<span className="vx-s2-livewho"><CalendarClock size={14} aria-hidden /> Schedule…</span>}
            items={rooms.map((r) => ({ key: r.id, label: `${r.title || "Room"} · ${r.workspaceName || r.workspace}`, onSelect: () => setSchedule(r) }))} />
        </> : <>
          <p className="vx-s2-note">Coming.</p>
          <Button disabled data-schedule-coming="">Schedule for later · coming</Button>
        </>}
      </div>
      <div className="vx-s2-other">
        <span className="vx-s2-note">A Vexa call is the default.</span>
        <Menu label="Other options" align="end" trigger={<span className="vx-s2-note">Other options ▾</span>}
          items={[
            { key: "meet", label: "Google Meet — paste its link below", onSelect: onOtherPlatform },
            { key: "teams", label: "Microsoft Teams — paste its link below", onSelect: onOtherPlatform },
            { key: "zoom", label: "Zoom — paste its link below", onSelect: onOtherPlatform },
          ]} />
      </div>
      {schedule && <ScheduleCallDialog session={schedule.id} label={roomCallName(schedule.title || "")} workspace={schedule.workspace}
        workspaceName={schedule.workspaceName} onClose={() => setSchedule(null)} />}
    </section>
  );
}
