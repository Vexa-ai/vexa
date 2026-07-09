"use client";
/** Today — the Meetings surface's CENTER tab, v5 AGENDA TIMELINE (design-spec
 *  today-v5-agenda-timeline, owner-approved mockup). ONE time axis:
 *
 *    "Coming up"  — a day-grouped agenda card for the visible week (‹ › pages weeks). A live
 *                   meeting shows IN PLACE on its day row (green bar) — no separate NOW zone.
 *    past feed    — reverse-chron day-grouped rows below: faces · title · one state phrase.
 *
 *  RENDERING LAW (binding): a meeting row is ONE line — title + time (+ faces on past rows).
 *  The ONLY extra ink is ONE inline deviation phrase (`in meeting →` · `couldn't import` ·
 *  `no brief yet` · `recap ready`), each deep-linking to the right page. A prepared meeting is
 *  the quiet default and carries NO phrase. Depth lives on the per-state pages, not here.
 *  Supersedes the v4 zones (NOW/NEXT/THIS WEEK/LATER/TO REVIEW) — same intent, less vocabulary. */
import { useEffect, useState, useSyncExternalStore } from "react";
import { registerTab } from "../contributions";
import { Icon } from "../ui-kit";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { usePreviewPinTab } from "./previewPinTab";
import type { MeetingMock } from "./meetingModel";
import { useLiveMeetings, fetchDurableTranscript } from "./liveMeetings";
import { groupMeetings, type MeetingGroup } from "./meetingGroups";
import { meetingTab } from "./meeting";
import { MeetingsOnboarding } from "./meetingsOnboarding";

// ── reviewed-recaps store (localStorage) — opening a recap retires its phrase ──────────────────
const REVIEWED_KEY = "vexa.reviewedMeetings";
let reviewed: Set<string> = new Set();
try { reviewed = new Set(JSON.parse(localStorage.getItem(REVIEWED_KEY) ?? "[]") as string[]); } catch { /* fresh */ }
const reviewedSubs = new Set<() => void>();
let reviewedSnapshot: string[] = [...reviewed];
export function markReviewed(runId: string): void {
  if (reviewed.has(runId)) return;
  reviewed.add(runId);
  reviewedSnapshot = [...reviewed];
  try { localStorage.setItem(REVIEWED_KEY, JSON.stringify(reviewedSnapshot.slice(-500))); } catch { /* quota */ }
  reviewedSubs.forEach((f) => f());
}
function useReviewed(): Set<string> {
  useSyncExternalStore(
    (cb) => { reviewedSubs.add(cb); return () => reviewedSubs.delete(cb); },
    () => reviewedSnapshot,
    () => reviewedSnapshot,
  );
  return reviewed;
}

// ── pure timeline splitters (unit-tested offline) ─────────────────────────────────────────────
export interface AgendaDay { key: string; date: Date; groups: MeetingGroup[] }

const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const startOfDay = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };

/** The agenda window: day-grouped upcoming (+live) meetings for the week starting at
 *  today + 7·weekOffset days. Unscheduled plans attach to TODAY (offset 0 only) — they need
 *  attention but have no place on a time axis. Days without events are omitted, except today. */
export function agendaWindow(groups: MeetingGroup[], now: Date = new Date(), weekOffset = 0): AgendaDay[] {
  const start = startOfDay(now);
  start.setDate(start.getDate() + weekOffset * 7);
  const end = new Date(start); end.setDate(end.getDate() + 7);
  const days = new Map<string, AgendaDay>();
  const put = (d: Date, g: MeetingGroup) => {
    const k = dayKey(d);
    let day = days.get(k);
    if (!day) { day = { key: k, date: startOfDay(d), groups: [] }; days.set(k, day); }
    day.groups.push(g);
  };
  for (const g of groups) {
    if (g.phase !== "prep" && g.phase !== "live") continue;
    const at = g.current.scheduled_at ? new Date(g.current.scheduled_at) : null;
    if (at && Number.isFinite(at.getTime())) {
      // a live meeting always shows — clamp a stale/past schedule onto today so it can't vanish
      const slot = g.phase === "live" && at < start ? new Date(now) : at;
      if (slot >= start && slot < end) put(slot, g);
    } else if (weekOffset === 0) {
      put(new Date(now), g);   // unscheduled (or undated live) → today's row, "no time set"
    }
  }
  // today's row always renders on the current week so "No events today" is stated honestly
  if (weekOffset === 0 && !days.has(dayKey(now))) days.set(dayKey(now), { key: dayKey(now), date: startOfDay(now), groups: [] });
  const out = [...days.values()].sort((a, b) => a.date.getTime() - b.date.getTime());
  for (const day of out) {
    day.groups.sort((a, b) => (a.current.scheduled_at ?? "9999").localeCompare(b.current.scheduled_at ?? "9999"));
  }
  return out;
}

export interface PastEntry { run: MeetingMock; group: MeetingGroup }
export interface PastDay { key: string; date: Date; entries: PastEntry[] }

const PAST_CAP = 8;

/** The past feed: each meeting's newest finished run, newest first, day-grouped. */
export function pastFeed(groups: MeetingGroup[], cap: number = PAST_CAP): PastDay[] {
  const entries: PastEntry[] = groups
    .filter((g) => g.pastRuns[0] && (g.pastRuns[0].has_recording || g.pastRuns[0].start_time))
    .map((g) => ({ run: g.pastRuns[0], group: g }))
    .sort((a, b) => (b.run.start_time ?? "").localeCompare(a.run.start_time ?? ""))
    .slice(0, cap);
  const days = new Map<string, PastDay>();
  for (const e of entries) {
    const d = e.run.start_time ? new Date(e.run.start_time) : new Date();
    const k = dayKey(d);
    let day = days.get(k);
    if (!day) { day = { key: k, date: startOfDay(d), entries: [] }; days.set(k, day); }
    day.entries.push(e);
  }
  return [...days.values()].sort((a, b) => b.date.getTime() - a.date.getTime());
}

/** ONE deviation phrase per row (the rendering law) — or null for the quiet default. */
export function deviationPhrase(g: MeetingGroup): { text: string; tone: "live" | "danger" | "accent" } | null {
  const m = g.current;
  if (g.phase === "live") return { text: "in meeting →", tone: "live" };
  if (m.auto_join_error) return { text: "couldn’t import — no meeting link", tone: "danger" };
  if (!m.native_id && !m.meeting_url) return { text: "no meeting link", tone: "danger" };
  if (!m.workspace_id) return { text: "no brief yet", tone: "accent" };
  return null;
}

// ── rendering ─────────────────────────────────────────────────────────────────────────────────
const label = (m: MeetingMock) => m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "");

const toneColor = { live: "var(--green)", danger: "var(--danger)", accent: "var(--accent)" } as const;

function timeShort(at?: string): string {
  if (!at) return "no time set";
  try { return new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  catch { return "scheduled"; }
}

function Faces({ m }: { m: MeetingMock }) {
  const att = (m.attendees ?? []).slice(0, 3);
  if (!att.length) return null;
  const extra = (m.attendees?.length ?? 0) - att.length;
  const initials = (a: { email: string; name?: string }) =>
    (a.name ? a.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("") : a.email.slice(0, 2)).toUpperCase();
  return (
    <span style={{ display: "inline-flex", flex: "none" }}>
      {att.map((a, i) => (
        <span key={a.email} title={a.name || a.email}
          style={{ width: 20, height: 20, borderRadius: "50%", background: "var(--panel2)", color: "var(--t2)",
            display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 8, fontWeight: 700,
            border: "2px solid var(--bg)", marginLeft: i ? -6 : 0 }}>
          {initials(a)}
        </span>
      ))}
      {extra > 0 && (
        <span style={{ width: 20, height: 20, borderRadius: "50%", background: "var(--panel2)", color: "var(--t3)",
          display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 8, fontWeight: 700,
          border: "2px solid var(--bg)", marginLeft: -6 }}>+{extra}</span>
      )}
    </span>
  );
}

function EventRow({ g }: { g: MeetingGroup }) {
  const m = g.current;
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const dev = deviationPhrase(g);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick}
      style={{ display: "flex", alignItems: "baseline", gap: 9, padding: "3px 2px", cursor: "pointer", flexWrap: "wrap" }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, alignSelf: "center", flex: "none",
        background: g.phase === "live" ? "var(--green)" : "var(--panel2)" }} />
      <span style={{ fontSize: 13.5, fontWeight: 500, color: "var(--t1)", minWidth: 0,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(m)}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", flex: "none" }}>{timeShort(m.scheduled_at)}</span>
      {dev && (
        <span style={{ fontSize: 12, color: toneColor[dev.tone], borderBottom: `1px dotted ${toneColor[dev.tone]}`, flex: "none" }}>
          {dev.text}
        </span>
      )}
    </div>
  );
}

function DayRow({ day, isToday }: { day: AgendaDay; isToday: boolean }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "64px 1fr", gap: "0 14px", padding: "11px 14px",
      borderTop: "1px dashed var(--line)" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
        <span style={{ fontSize: 19, fontWeight: 600, color: "var(--t1)", fontVariantNumeric: "tabular-nums" }}>
          {day.date.getDate()}
        </span>
        <span style={{ fontSize: 10, color: "var(--t3)", lineHeight: 1.3 }}>
          {day.date.toLocaleString(undefined, { month: "long" })}
          {isToday && <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", marginLeft: 3, verticalAlign: 4 }} />}
          <br />{day.date.toLocaleString(undefined, { weekday: "short" })}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        {day.groups.length === 0
          ? <span style={{ fontSize: 12.5, color: "var(--t3)", padding: "2px 0" }}>No events today</span>
          : day.groups.map((g) => <EventRow key={g.key} g={g} />)}
      </div>
    </div>
  );
}

function PastLine({ m }: { m: MeetingMock }) {
  const [line, setLine] = useState<string>("");
  useEffect(() => {
    let on = true;
    if (!m.has_recording) { setLine(""); return; }
    fetchDurableTranscript(m.id)
      .then((t) => {
        if (!on) return;
        if (t.notes.length) setLine(`${t.notes.length} notes`);
        else if (t.lines?.length) setLine(`${t.lines.length} lines`);
      })
      .catch(() => {});
    return () => { on = false; };
  }, [m.id, m.has_recording]);
  const dur = (() => {
    if (!m.start_time || !m.end_time) return "";
    const min = Math.round((new Date(m.end_time).getTime() - new Date(m.start_time).getTime()) / 60000);
    return min > 0 ? `${min}m` : "";
  })();
  const when = m.start_time
    ? new Date(m.start_time).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
  return <>{[when, dur, line].filter(Boolean).join(" · ")}</>;
}

function PastRow({ e, reviewedIds }: { e: PastEntry; reviewedIds: Set<string> }) {
  const run = e.run;
  const layout = useService(LayoutServiceId);
  const open = () => { markReviewed(run.id); layout.openTab(meetingTab(run)); };
  const unreviewed = !reviewedIds.has(run.id);
  const phrase = run.has_recording
    ? (unreviewed ? { text: "recap ready", color: "var(--accent)" } : null)
    : { text: "nothing captured", color: "var(--t3)" };
  return (
    <div onClick={open}
      style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", alignItems: "center", gap: 10,
        padding: "6px 8px", borderRadius: 7, cursor: "pointer" }}
      onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel)")}
      onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}>
      <Faces m={run.attendees?.length ? run : e.group.current} />
      <span style={{ display: "flex", gap: 9, alignItems: "baseline", minWidth: 0, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label(run)}
        </span>
        {phrase && (
          <span style={{ fontSize: 11.5, color: phrase.color, borderBottom: `1px dotted ${phrase.color}`, flex: "none" }}>
            {phrase.text}
          </span>
        )}
      </span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", flex: "none" }}><PastLine m={run} /></span>
    </div>
  );
}

// ── the tab ───────────────────────────────────────────────────────────────────────────────────
function TodayView() {
  const meetings = useLiveMeetings();
  const reviewedIds = useReviewed();
  const [weekOffset, setWeekOffset] = useState(0);
  const now = new Date();
  const groups = groupMeetings(meetings);
  const days = agendaWindow(groups, now, weekOffset);
  const past = pastFeed(groups);
  const todayKey = dayKey(now);
  const empty = meetings.length === 0;
  const pager = { background: "none", border: "1px solid var(--line)", color: "var(--t2)", borderRadius: 6,
    width: 24, height: 24, cursor: "pointer", fontSize: 13, lineHeight: 1 } as const;
  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "18px 22px" }}>
      <div style={{ maxWidth: 720 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 17, fontWeight: 700, color: "var(--t1)", flex: 1 }}>Coming up</span>
          {weekOffset > 0 && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)" }}>
              {days[0]?.date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) ?? ""} →
            </span>
          )}
          <button aria-label="previous week" style={{ ...pager, opacity: weekOffset === 0 ? 0.4 : 1 }}
            disabled={weekOffset === 0} onClick={() => setWeekOffset((v) => Math.max(0, v - 1))}>‹</button>
          <button aria-label="next week" style={pager} onClick={() => setWeekOffset((v) => Math.min(8, v + 1))}>›</button>
        </div>

        {empty ? (
          /* user onboarding, frame 4: three paths (calendar primary / plan / drop bot) */
          <MeetingsOnboarding variant="full" />
        ) : (
          <>
            {/* the STANDING calendar affordance — stays while this user has no calendar connected */}
            <MeetingsOnboarding variant="slim" />
            <div style={{ marginTop: 14, border: "1px solid var(--line)", borderRadius: 12, background: "var(--panel)" }}>
              {days.length === 0
                ? <div style={{ padding: "14px 16px", fontSize: 12.5, color: "var(--t3)" }}>Nothing this week.</div>
                : days.map((d) => <DayRow key={d.key} day={d} isToday={d.key === todayKey} />)}
            </div>
          </>
        )}

        {past.map((day) => (
          <div key={day.key}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase",
              color: "var(--t3)", margin: "18px 0 4px" }}>
              {day.date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
            </div>
            {day.entries.map((e) => <PastRow key={e.run.id} e={e} reviewedIds={reviewedIds} />)}
          </div>
        ))}

        {!empty && (
          <div style={{ fontSize: 11.5, color: "var(--t3)", margin: "24px 2px 10px", lineHeight: 1.5 }}>
            Older meetings live in Knowledge — ask the agent about anything that was said or decided.
          </div>
        )}
      </div>
    </div>
  );
}

registerTab("today", TodayView);
