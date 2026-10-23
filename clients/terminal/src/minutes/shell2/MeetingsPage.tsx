"use client";
/** THE MEETINGS PAGE (UI 7w, 7y, 7t; feature `shell2`). "Meetings", not "Calls" — "call" is only the
 *  live state.
 *
 *  - **Start a meeting**: the view's one primary action, no workspace picker — a meeting can be just
 *    a meeting (in My desk), filed later.
 *  - **Send Vexa to a meeting**: its own block, one input that takes any string. A link or code that
 *    parses sends the bot (`POST /api/bots`, the same edge as everywhere else); anything else opens an
 *    agent chat with the string, which resolves it (calendar, a question) and then sends the bot.
 *  - **Live**, pinned on top; **Upcoming** from the calendar, each with its "Vexa joins" switch
 *    (`auto_join` on the planned meeting) — a click opens its prep page on the right; **Past
 *    meetings** with search, a workspace and a person filter, and week/month groups, 50 at a time.
 *
 *  Every row opens in the right panel (7d). The rows are the meetings store — nothing else is read. */
import { copyRefItem } from "../readme/ObjectActions";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Bot, MoreHorizontal, Phone } from "lucide-react";
import { AvatarStack, Badge, Button, EntityChip, Input, ListRow, Menu, RowCell, SectionLabel, Select, StatusDot, Switch, formatDate } from "../../ui-kit";
import { WorkspaceMark } from "../rooms/WorkspaceMark";
import { openWorkspaceView } from "../desktop/home2/workspaceView";
import { usePeoplePhotos } from "./photos";
import { firstOrYou, nameOrYou } from "./me";
import { openPerson } from "./personOpen";
import { ASK_CHAT_EVENT, NEW_CHAT_AT_EVENT, OPEN_MEETING_EVENT } from "../../platform/events";
import { refreshMeetings, useLiveMeetings, useLiveMeetingsLoaded } from "../../surfaces/liveMeetings";
import { parseMeetingInput } from "../../surfaces/meetingId";
import { getJitsiHosts } from "../../surfaces/jitsiHosts";
import { defaultBotName } from "../../surfaces/defaultBotName";
import { updatePlannedMeeting } from "../../surfaces/plannedApi";
import type { MeetingMock } from "../../surfaces/meetingModel";
import { callLabel, platformLabel } from "../callsList";
import { NO_FILTERS, shortWhen, durationMin, groupPast, liveMeetings, matchMeetings, pastMeetings, peopleOf, upcomingMeetings, type Filters } from "./meetings";
import { useWorkspaceNames } from "./names";
import { getCall } from "../../surfaces/callsApi";
import { StartMeeting } from "./StartMeeting";
import { elapsed, endedLine, inNowLine, recentCalls, rowLabel } from "./callNames";
import { isMe } from "./me";
import "./shell2.css";

const PAGE = 50;
const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const openMeeting = (m: MeetingMock) => fire(OPEN_MEETING_EVENT, { ref: String(m.id) });

type Sent = { state: "sending" | "sent" | "failed"; text: string };
/** platform names short enough for the 52px column; the full name is on the meeting page */
const SHORT: Record<string, string> = { "Google Meet": "Meet", Teams: "Teams", Zoom: "Zoom", Jitsi: "Jitsi" };
const shortPlatform = (p?: string) => { const l = platformLabel(p); return SHORT[l] ?? l; };
/** the attendees by FACE AND NAME (founder 2026-10-17): the AvatarStack (each face opens the person)
 *  and the first names as text, "with Ines, Dan +2"; dropped from the columns in a narrow pane, where
 *  the names join the row's detail instead */
const withNames = (names: string[]) => names.length ? `with ${names.slice(0, 2).map((n) => firstOrYou(n)).join(", ")}${names.length > 2 ? ` +${names.length - 2}` : ""}` : "";
const Faces = ({ m }: { m: MeetingMock }) => { const p = peopleOf(m); return <RowCell w={160} hideNarrow>{p.length ? <span className="vx-s2-faces">
  <AvatarStack names={p} max={2} onOpen={(n) => openPerson({ name: n })} label={(n) => nameOrYou(n)} /><span className="vx-s2-faces-names">{withNames(p)}</span></span> : null}</RowCell>; };
/** a workspace reference: an EntityChip with the workspace's own mark; it opens the workspace page */
function WsChip({ slug, name }: { slug: string; name: string }) {
  return <EntityChip kind="workspace" icon={<WorkspaceMark id={slug} name={name} size={16} />} title={name}
    onOpen={() => void openWorkspaceView({ slug, name })}>{name}</EntityChip>;
}
/** ⋯ on a meeting row: the object's own acts (Open; more land with share and references) */
const MoreCell = ({ m }: { m: MeetingMock }) => <RowCell w={28}><Menu label={`More for ${callLabel(m)}`} align="end" variant="ghost"
  trigger={<MoreHorizontal size={14} strokeWidth={1.75} aria-hidden />} items={[{ key: "open", label: "Open", onSelect: () => openMeeting(m) },
    ...(/^\d+$/.test(String(m.id)) ? [copyRefItem({ kind: "meeting", id: String(m.id), label: callLabel(m) })] : [])]} /></RowCell>;

/** The send block: a link sends the bot; anything else goes to an agent chat. */
function SendVexa({ inputRef }: { inputRef?: RefObject<HTMLDivElement | null> }) {
  const [text, setText] = useState("");
  const [sent, setSent] = useState<Sent | null>(null);
  const send = async () => {
    const raw = text.trim();
    if (!raw || sent?.state === "sending") return;
    const parsed = parseMeetingInput(raw, await getJitsiHosts().catch(() => []));
    if (!parsed) {
      // NOT A LINK (7t): an agent chat with the string attached resolves it, then sends the bot
      fire(NEW_CHAT_AT_EVENT, { workspace: "" });
      window.setTimeout(() => fire(ASK_CHAT_EVENT, {
        prompt: `Send Vexa to this meeting: “${raw}”. Find it (my calendar, or ask me which one), then send the bot.`,
        display: `Send Vexa to: ${raw}`,
      }), 150);
      setText(""); setSent(null);
      return;
    }
    setSent({ state: "sending", text: raw });
    try {
      const r = await fetch("/api/bots", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: parsed.platform, native_meeting_id: parsed.native_meeting_id, meeting_url: raw, bot_name: defaultBotName() }) });
      if (!r.ok) throw new Error(r.status === 409 ? "That meeting already has Vexa in it." : r.status === 429 ? "You're at your meeting limit — stop one first." : "Couldn't send Vexa. Try again.");
      setSent({ state: "sent", text: "Vexa is on its way — admit it in the meeting." }); setText("");
      refreshMeetings(); window.setTimeout(refreshMeetings, 2000); window.setTimeout(refreshMeetings, 6000);
    } catch (e) { setSent({ state: "failed", text: (e as Error).message || "Couldn't send Vexa. Try again." }); }
  };
  return (
    <form className="vx-s2-send" data-send-vexa="" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <div className="vx-s2-send-row" ref={inputRef}>
        <Input label="Send Vexa to a meeting" value={text} onChange={(e) => { setText(e.target.value); if (sent?.state !== "sending") setSent(null); }}
          placeholder="Paste a meeting link, or describe the meeting"
          error={sent?.state === "failed" ? sent.text : undefined}
          hint={sent?.state === "sent" ? sent.text : text.trim() ? "A link sends Vexa; anything else asks an agent to find the meeting" : undefined} />
        <Button type="submit" variant="secondary" size="md" icon={<Bot size={14} />} disabled={!text.trim()} loading={sent?.state === "sending"}>Send</Button>
      </div>
    </form>
  );
}

function UpcomingRow({ m }: { m: MeetingMock }) {
  const [on, setOn] = useState(m.auto_join !== false);
  const [busy, setBusy] = useState(false);
  const people = peopleOf(m);
  const toggle = async () => {
    const next = !on;
    setOn(next); setBusy(true);
    try { await updatePlannedMeeting(m.id, { auto_join: next }); refreshMeetings(); } catch { setOn(!next); }
    setBusy(false);
  };
  return (
    <div className="vx-s2-mrow" data-upcoming-row={m.id}>
      <ListRow size="md" strong narrowDetail title={rowLabel(m, callLabel(m))} label={`Open the prep page: ${callLabel(m)}`} onSelect={() => openMeeting(m)}
        detail={[m.scheduled_at ? formatDate(m.scheduled_at) : "", platformLabel(m.platform), withNames(people)].filter(Boolean).join(" · ") || undefined}
        meta={<><RowCell w={72} align="end">{shortWhen(m.scheduled_at)}</RowCell><RowCell w={52}>{shortPlatform(m.platform)}</RowCell></>}
        trailing={<><Faces m={m} /><RowCell w={140} align="end"><Switch checked={on} disabled={busy} label="Vexa joins" onChange={() => void toggle()} /></RowCell></>} />
    </div>
  );
}

/** A LIVE ROW (founder O1, 2026-10-18): who is in the call now (faces and first names) and for how
 *  long. A Vexa call is read from its session (`GET /api/sessions/{s}/call` → `in_now`, never the
 *  listener) every few seconds while the row is shown; a bot meeting shows its attendees. "renamed"
 *  when its topic renamed it. */
function LiveRow({ m, wsName }: { m: MeetingMock; wsName: (s: string) => string }) {
  const [inNow, setInNow] = useState<string[] | null>(null);
  const [, tick] = useState(0);
  useEffect(() => {
    const sid = m.call_session;
    let on = true;
    const read = () => { if (sid) void getCall(sid).then((c) => { if (on) setInNow((c.call?.in_now ?? []).map((p) => p.name || p.subject).filter(Boolean)); }).catch(() => undefined); };
    read();
    const poll = window.setInterval(read, 5000);
    const clock = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => { on = false; window.clearInterval(poll); window.clearInterval(clock); };
  }, [m.call_session]);
  const since = Date.parse(m.start_time ?? m.created_at ?? "");
  const names = inNow ?? peopleOf(m);
  const detail = [m.platform === "vexa_call" ? inNowLine(names, (n) => isMe(n)) : "", Number.isFinite(since) ? elapsed(since) : "",
    m.renamed_from ? "renamed" : "", m.workspace_id ? wsName(m.workspace_id) : ""].filter(Boolean).join(" · ");
  return <div className="vx-s2-mrow" data-live-row={m.id}>
    <ListRow size="md" strong leading={<StatusDot tone="success" label="" />} title={rowLabel(m, callLabel(m))} onSelect={() => openMeeting(m)}
      detail={detail || undefined} label={m.renamed_from ? `${callLabel(m)} (renamed from ${m.renamed_from})` : undefined}
      trailing={<><RowCell w={160} hideNarrow>{names.length ? <span className="vx-s2-faces">
        <AvatarStack names={names} max={3} onOpen={(n) => openPerson({ name: n })} label={(n) => nameOrYou(n)} /></span> : null}</RowCell>
        <RowCell w={140} align="end"><Badge tone="success" dot>Live</Badge></RowCell><MoreCell m={m} /></>} />
  </div>;
}

export function MeetingsPage() {
  usePeoplePhotos();
  const all = useLiveMeetings();
  const loaded = useLiveMeetingsLoaded();
  const wsName = useWorkspaceNames();
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [limit, setLimit] = useState(PAGE);
  const [moreUp, setMoreUp] = useState(false);
  const live = liveMeetings(all);
  const upcoming = upcomingMeetings(all);
  const past = pastMeetings(all);
  const matched = matchMeetings(past, f, wsName);
  const shown = matched.slice(0, limit);
  const groups = groupPast(shown);
  const filtered = f.q.trim() !== "" || f.workspace !== "all" || f.person !== "any";
  const workspaces = useMemo(() => [...new Set(past.map((m) => m.workspace_id).filter((x): x is string => !!x && x !== "personal"))], [past]);
  const people = useMemo(() => [...new Set(past.flatMap(peopleOf))].sort(), [past]);
  const set = (p: Partial<Filters>) => { setF((x) => ({ ...x, ...p })); setLimit(PAGE); };
  const soon = upcoming.slice(0, 5);
  const recent = recentCalls(all);
  const [chooser, setChooser] = useState(false);
  const sendRef = useRef<HTMLDivElement | null>(null);
  const focusSend = () => { setChooser(false); window.setTimeout(() => sendRef.current?.querySelector("input")?.focus(), 0); };
  return (
    <div className="vx-s2-page vx-s2-meetings" data-meetings-page="">
      <header className="vx-s2-pagehead">
        <span className="vx-s2-pagecount">{past.length} {past.length === 1 ? "meeting" : "meetings"}</span>
        <span className="vx-panelhead-gap" />
        <Button variant="primary" icon={<Phone size={14} />} data-start-meeting="" aria-expanded={chooser} onClick={() => setChooser(!chooser)}>Start a meeting</Button>
      </header>
      {/* THREE CHOICES (founder O1, 2026-10-18): call a person, start in a room, schedule for later */}
      {chooser && <StartMeeting onClose={() => setChooser(false)} onOtherPlatform={focusSend} />}
      <SendVexa inputRef={sendRef} />
      {live.length > 0 && <section className="vx-s2-sec" data-live="">
        <SectionLabel as="h3">Live</SectionLabel>
        {live.map((m) => <LiveRow key={m.id} m={m} wsName={wsName} />)}
      </section>}
      {/* LIVE → RECENT: a call ends when the last person leaves and comes here, saying so */}
      {recent.length > 0 && <section className="vx-s2-sec" data-recent="">
        <SectionLabel as="h3">Recent</SectionLabel>
        {recent.map((m) => <div key={m.id} className="vx-s2-mrow" data-recent-row={m.id}>
          <ListRow size="md" strong title={rowLabel(m, callLabel(m))} onSelect={() => openMeeting(m)}
            detail={[endedLine(m), m.renamed_from ? "renamed" : ""].filter(Boolean).join(" · ")}
            trailing={<><Faces m={m} /><MoreCell m={m} /></>} />
        </div>)}
      </section>}
      <section className="vx-s2-sec" data-upcoming="">
        <SectionLabel as="h3">Upcoming</SectionLabel>
        {upcoming.length === 0
          ? <p className="vx-s2-note">{loaded ? "Nothing planned. Meetings from your calendar show here, each with “Vexa joins”." : "Loading…"}</p>
          : <>{(moreUp ? upcoming : soon).map((m) => <UpcomingRow key={m.id} m={m} />)}
            {upcoming.length > soon.length && <Button variant="ghost" onClick={() => setMoreUp(!moreUp)}>{moreUp ? "Show fewer" : `Show ${upcoming.length - soon.length} more`}</Button>}</>}
      </section>
      <section className="vx-s2-sec" data-past="">
        <SectionLabel as="h3">Past meetings</SectionLabel>
        <div className="vx-s2-tools">
          <Input label="Search meetings" hideLabel control="sm" value={f.q} onChange={(e) => set({ q: e.target.value })} placeholder="Search by title, people or workspace" />
          <Select label="Workspace" control="sm" value={f.workspace} onChange={(v) => set({ workspace: v })}
            options={[{ value: "all", label: "All workspaces" }, { value: "personal", label: wsName("personal") }, ...workspaces.map((w) => ({ value: w, label: wsName(w) }))]} />
          <Select label="Person" control="sm" value={f.person} onChange={(v) => set({ person: v })} options={[{ value: "any", label: "Anyone" }, ...people.map((p) => ({ value: p, label: p }))]} />
        </div>
        {filtered && <p className="vx-s2-note" aria-live="polite">{matched.length} of {past.length}</p>}
        {groups.map((g) => <div key={g.key} className="vx-s2-group" data-group={g.key}>
          <SectionLabel as="h3">{g.label}</SectionLabel>
          {g.rows.map((m) => { const mins = durationMin(m); const at = m.start_time ?? m.scheduled_at ?? m.created_at;
            // FIXED COLUMNS (ListRow + RowCell): title · time 72 · length 52 · workspace 160 · faces and names 160 · ⋯ 28;
            // in a narrow pane the title takes line 1 and the time, length and workspace go under it
            return <div key={m.id} className="vx-s2-mrow" data-past-row={m.id}>
              <ListRow size="md" strong narrowDetail title={rowLabel(m, callLabel(m))} onSelect={() => openMeeting(m)}
                detail={[at ? formatDate(at) : "", mins ? `${mins} min` : "", withNames(peopleOf(m)), m.workspace_id ? wsName(m.workspace_id) : ""].filter(Boolean).join(" · ") || undefined}
                meta={<><RowCell w={72} align="end">{shortWhen(at)}</RowCell><RowCell w={52}>{mins ? `${mins} min` : ""}</RowCell></>}
                trailing={<><RowCell w={160} hideNarrow>{m.workspace_id ? <WsChip slug={m.workspace_id} name={wsName(m.workspace_id)} /> : null}</RowCell>
                  <Faces m={m} /><MoreCell m={m} /></>} />
            </div>; })}
        </div>)}
        {matched.length > shown.length && <Button variant="ghost" data-show-more="" onClick={() => setLimit((n) => n + PAGE)}>Show more · {matched.length - shown.length} left</Button>}
        {loaded && past.length === 0 && <p className="vx-s2-note">No meetings yet. Start one, or send Vexa to a meeting.</p>}
      </section>
    </div>
  );
}
