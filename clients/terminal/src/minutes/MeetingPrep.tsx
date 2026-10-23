"use client";
/** PREP IN THE MEETING PANEL (feature `meeting_prep`; validation log O2, founder-felt 2026-10-18 on the
 *  grounded lab: "feels good"). A planned meeting's panel shows, above its Notes, what is gathered for
 *  it: my goal, the people with where we know them from, what is open from last time, the agenda, the
 *  material, and three readiness ticks.
 *
 *  A READ PROJECTION, NOT A STORE (architecture review 2026-10-18 §1): every line is read from its
 *  owner by `prepRead.ts` — the meeting row, the people the viewer knows, the card Inbox, the
 *  meeting's note page. The one act here is the person's own: setting the goal writes `goals:` into
 *  the meeting's page (minted first when it has none). Material and agenda are added in the meeting's
 *  chat or on the page; there is no separate prep form. Real kit only. */
import { useMemo, useState } from "react";
import "./meetingPrep.css";
import { Circle, CircleCheck, Lock, Users } from "lucide-react";
import { Badge, Button, Input, ListRow, SectionLabel } from "../ui-kit";
import { RefChip } from "../ui-kit/RefChip";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { usePeople } from "./shell2/PeoplePage";
import { PersonChip } from "./shell2/PersonChip";
import { meNow } from "./shell2/me";
import { useCardInbox } from "./cards";
import { openCardSheet } from "./cards/api";
import { createMeetingPage } from "./meetingNote";
import { readWorkspaceFile, writeWorkspaceFile } from "../data/workspaceApi";
import { DESK_SLUG } from "./workspaceReadme";
import {
  openFromLast, prepPeople, previousOccurrence, readAgenda, readGoal, readMaterial, readiness,
  writeGoal, type RowLike,
} from "./prepRead";

export type PrepNotes =
  | { phase: "loading" | "none" | "error" }
  | { phase: "ready"; path: string; workspace: string; body: string; readonly?: boolean };

/** The goal is private when the page is the person's desk copy; on a page in a shared workspace it is
 *  shared with that workspace, and the section says so. */
const onDesk = (ws: string) => !ws || ws === DESK_SLUG || ws === "personal";

export function MeetingPrep({ meetingId, notes, reload }: { meetingId: string; notes: PrepNotes; reload: () => void }) {
  const rows = useLiveMeetings();
  const row = rows.find((m) => m.id === meetingId || m.native_id === meetingId) as (RowLike & { workspace_id?: string }) | undefined;
  const { people: known } = usePeople();
  const inbox = useCardInbox();
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const body = notes.phase === "ready" ? notes.body : "";
  const goal = readGoal(body);
  const agenda = useMemo(() => readAgenda(body), [body]);
  const material = useMemo(() => readMaterial(body), [body]);
  const people = useMemo(() => prepPeople(row?.attendees, known, meNow()), [row?.attendees, known]);
  const prev = useMemo(() => (row ? previousOccurrence(rows as RowLike[], row) : null), [rows, row]);
  const open = useMemo(() => openFromLast(inbox.items, prev), [inbox.items, prev]);
  const r = readiness(goal, people, material);
  const shared = notes.phase === "ready" && !onDesk(notes.workspace);
  const canWrite = notes.phase === "none" || (notes.phase === "ready" && !notes.readonly);

  const setGoal = async () => {
    const g = text.trim();
    if (!g || saving) return;
    setSaving(true); setError("");
    try {
      // the page the view reads, where it reads it; a meeting with no page yet gets its page first
      let path: string, ws: string, current: string;
      if (notes.phase === "ready") { path = notes.path; ws = notes.workspace; current = notes.body; }
      else {
        const minted = await createMeetingPage(meetingId);
        path = minted.path; ws = minted.workspace;
        current = (await readWorkspaceFile(path, { slug: ws || DESK_SLUG })) ?? "";
      }
      await writeWorkspaceFile(path, writeGoal(current, g), { slug: ws || DESK_SLUG });
      setText(""); reload();
    } catch (e) { setError(e instanceof Error && e.message ? e.message : "Could not save the goal. Retry."); }
    finally { setSaving(false); }
  };

  const tick = (ok: boolean, l: string) => (
    <span className="vx-mv-meta vx-prep-tick" data-prep-tick={l.toLowerCase()} data-ok={ok ? "" : undefined}>
      {ok ? <CircleCheck size={13} aria-hidden /> : <Circle size={13} aria-hidden />}{l}</span>);

  return (
    <section data-meeting-prep="" aria-label="Prep" className="vx-prep">
      <div className="vx-prep-head">
        <SectionLabel as="h2">Prep</SectionLabel>
        {tick(r.goal, "Goal")}{tick(r.people, `People${people.length ? ` ${people.length}` : ""}`)}{tick(r.material, "Material")}
        <span className="vx-mv-meta" data-prep-state="">{r.ready ? "Ready" : "Gathering"}</span>
      </div>

      <div data-prep-goal="">
        <div className="vx-prep-row">
          <SectionLabel>My goal</SectionLabel>
          {shared ? <Badge tone="neutral"><Users size={11} aria-hidden /> on the shared page</Badge>
            : <Badge tone="neutral"><Lock size={11} aria-hidden /> only you</Badge>}
        </div>
        {goal ? <p className="vx-mv-meta vx-prep-line">{goal}</p>
          : canWrite ? <form className="vx-prep-form" onSubmit={(e) => { e.preventDefault(); void setGoal(); }}>
              <Input label="Your goal for this meeting" hideLabel control="sm" placeholder="What do you want from this meeting?" value={text} onChange={(e) => setText(e.target.value)} />
              <Button type="submit" disabled={!text.trim() || saving}>{saving ? "Saving…" : "Set goal"}</Button></form>
          : <p className="vx-mv-meta vx-prep-line">Not set.</p>}
        {error && <p role="alert" className="vx-mv-meta vx-mv-error vx-prep-note">{error}</p>}
      </div>

      <div data-prep-people="">
        <SectionLabel>People{people.length ? ` · ${people.length}` : ""}</SectionLabel>
        {people.length === 0 ? <p className="vx-mv-meta vx-prep-line">No one else is invited yet.</p>
          : people.map((p) => <ListRow key={p.email} size="md" strong
              title={<PersonChip person={{ name: p.name, email: p.email, subject: p.subject, key: p.key }} />}
              detail={[p.role, p.known ? "" : "not in your contacts yet"].filter(Boolean).join(" · ") || p.email}
              trailing={p.sources.length ? <span className="vx-prep-chips">{p.sources.map((s) => <Badge key={s} tone="neutral">{s}</Badge>)}</span> : undefined} />)}
      </div>

      {open.length > 0 && <div data-prep-open="">
        <SectionLabel>Open from last time · {open.length}</SectionLabel>
        {open.map((c) => <ListRow key={c.id} size="md" title={c.title} detail={[c.next, c.due ? `due ${c.due.slice(0, 10)}` : ""].filter(Boolean).join(" · ") || undefined}
          onSelect={() => openCardSheet(c.id)} />)}
      </div>}

      {agenda.length > 0 && <div data-prep-agenda="">
        <SectionLabel>Agenda · {agenda.length}</SectionLabel>
        <ol className="vx-prep-agenda">
          {agenda.map((a, i) => <li key={`${i}-${a}`}>{a}</li>)}</ol>
      </div>}

      <div data-prep-material="">
        <SectionLabel>Material</SectionLabel>
        {material.length === 0 ? <p className="vx-mv-meta vx-prep-line">Nothing added yet. Ask in the meeting's chat to add a page.</p>
          : <div className="vx-prep-material">{material.map((m) => <RefChip key={m} text={m} />)}</div>}
      </div>
    </section>
  );
}
