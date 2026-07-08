"use client";
/** Today — the Meetings surface's CENTER tab, v4 FLOW QUEUE (design-spec meeting-lifecycle-v2 §v4,
 *  approved mockup §08b). Renders MEETINGS (grouped runs — `meetingGroups.ts`), not raw rows, as
 *  five zones ordered by attention:
 *
 *    NOW        — the live capture (0–1 usually), green; shared-capture line when a workspace is bound.
 *    NEXT       — ONE expanded card holding ALL the prep affordances (draft brief / add link / arm warn).
 *    THIS WEEK  — dense one-line glyph rows (title · day/time · brief ✓/– · bot ✓/– · ws ✓); the whole
 *                 row clicks through to prep. NO pills, NO buttons — state chrome only on deviation.
 *    LATER      — a count line ("N more after Sunday ▸"), expandable.
 *    TO REVIEW  — ended-but-unreviewed recaps; a meeting LEAVES the list once its recap is opened.
 *
 *  No archive list: older meetings live in Knowledge — ask the agent. */
import { useEffect, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { registerTab } from "../contributions";
import { Icon } from "../ui-kit";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { usePreviewPinTab } from "./previewPinTab";
import { meetingPhase, type MeetingMock } from "./meetingModel";
import { useLiveMeetings, fetchDurableTranscript } from "./liveMeetings";
import { groupMeetings, type MeetingGroup } from "./meetingGroups";
import { meetingTab } from "./meeting";
import { readWorkspaceFile } from "./workspaceApi";
import { ASK_CHAT_EVENT } from "../canvas/actions";

// ── reviewed-recaps store (localStorage) — opening a recap retires it from TO REVIEW ──────────
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

// ── the pure zone split (unit-tested offline) ─────────────────────────────────────────────────
export interface TodayZones {
  now: MeetingGroup[];        // live captures
  next: MeetingGroup | null;  // THE one expanded upcoming meeting
  thisWeek: MeetingGroup[];   // the rest due before the coming Sunday ends (unscheduled plans too)
  later: MeetingGroup[];      // upcoming after Sunday
  toReview: MeetingGroup[];   // groups whose newest finished run is unreviewed
}

const REVIEW_CAP = 8;

/** Local end of the coming Sunday (today, if today IS Sunday). */
export function endOfWeek(now: Date): Date {
  const d = new Date(now);
  d.setDate(d.getDate() + ((7 - d.getDay()) % 7));
  d.setHours(23, 59, 59, 999);
  return d;
}

/** A finished run worth reviewing: it actually ran (or captured something). */
const reviewable = (m: MeetingMock): boolean => !!(m.has_recording || m.start_time);

export function todayZones(groups: MeetingGroup[], now: Date = new Date(), reviewedIds: Set<string> = reviewed): TodayZones {
  const live: MeetingGroup[] = [];
  const upcoming: MeetingGroup[] = [];
  for (const g of groups) {
    if (g.phase === "live") live.push(g);
    else if (g.phase === "prep") upcoming.push(g);
  }
  // soonest first; unscheduled plans right after the scheduled ones (they need attention)
  upcoming.sort((a, b) => {
    const sa = a.current.scheduled_at, sb = b.current.scheduled_at;
    if (!sa && !sb) return 0;
    if (!sa) return 1;
    if (!sb) return -1;
    return sa.localeCompare(sb);
  });
  const weekEnd = endOfWeek(now).getTime();
  const inWeek = (g: MeetingGroup) => {
    const at = g.current.scheduled_at;
    if (!at) return true;                            // no time set — keep visible, it needs attention
    const t = new Date(at).getTime();
    return !Number.isFinite(t) || t <= weekEnd;
  };
  const next = upcoming[0] ?? null;
  const rest = upcoming.slice(1);
  // TO REVIEW: any group's newest finished run that wasn't opened yet — including groups whose
  // NEXT occurrence is already planned (the recap is still owed).
  const toReview = groups
    .filter((g) => g.phase !== "live" && g.pastRuns[0] && reviewable(g.pastRuns[0]) && !reviewedIds.has(g.pastRuns[0].id))
    .sort((a, b) => (b.pastRuns[0].start_time ?? "").localeCompare(a.pastRuns[0].start_time ?? ""))
    .slice(0, REVIEW_CAP);
  return { now: live, next, thisWeek: rest.filter(inWeek), later: rest.filter((g) => !inWeek(g)), toReview };
}

// ── rendering ─────────────────────────────────────────────────────────────────────────────────
const label = (m: MeetingMock) => m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "");
const hasLink = (m: MeetingMock) => !!(m.native_id || m.meeting_url);
const botArmed = (m: MeetingMock) => hasLink(m) && m.auto_join !== false;

function dayTime(at?: string): string {
  if (!at) return "no time set";
  try {
    const d = new Date(at);
    return d.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return "scheduled"; }
}

function durationLabel(m: MeetingMock): string {
  if (!m.start_time || !m.end_time) return "";
  const ms = new Date(m.end_time).getTime() - new Date(m.start_time).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const min = Math.round(ms / 60000);
  return min >= 60 ? `${Math.floor(min / 60)}h ${min % 60}m` : `${min} min`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase",
        color: "var(--t3)", margin: "20px 0 8px" }}>{title}</div>
      {children}
    </>
  );
}

/** Does the meeting's prep brief exist yet? null while loading / no workspace to look in. */
function useBriefExists(m: MeetingMock): boolean | null {
  const [exists, setExists] = useState<boolean | null>(m.workspace_id ? null : false);
  useEffect(() => {
    if (!m.workspace_id) { setExists(false); return; }
    let alive = true;
    void readWorkspaceFile(`meetings/${m.id}/prep.md`, { slug: m.workspace_id })
      .then((t) => { if (alive) setExists(!!t); })
      .catch(() => { if (alive) setExists(false); });
    return () => { alive = false; };
  }, [m.id, m.workspace_id]);
  return exists;
}

// ── NOW — the live capture card ───────────────────────────────────────────────────────────────
function NowCard({ g }: { g: MeetingGroup }) {
  const m = g.current;
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const inRoom = m.live_status === "active";
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick}
      style={{ border: "1px solid var(--green)", background: "var(--panel)", borderRadius: 10,
        padding: "12px 15px", marginBottom: 10, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", boxShadow: "0 0 0 3px var(--greenbg)", flex: "none" }} />
        <span style={{ fontSize: 14, fontWeight: 650, color: "var(--t1)", flex: 1, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(m)}</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--green)", flex: "none" }}>live</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--t2)", marginTop: 5, paddingLeft: 16 }}>
        {inRoom ? "The bot is in the room — transcript is streaming." : `Bot ${m.live_status ?? "on its way"}.`}
      </div>
      <div style={{ fontSize: 12, color: m.workspace_id ? "var(--green)" : "var(--t3)", marginTop: 2, paddingLeft: 16 }}>
        {m.workspace_id
          ? `Shared live capture — notes are landing in ${m.workspace_id} as they happen.`
          : "Capturing for you only — bind a workspace to share the live notes."}
      </div>
    </div>
  );
}

// ── NEXT — the one expanded card carrying ALL the prep affordances ────────────────────────────
function NextCard({ g }: { g: MeetingGroup }) {
  const m = g.current;
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const layout = useService(LayoutServiceId);
  const brief = useBriefExists(m);
  const armed = botArmed(m);
  const openPrep = (e: React.MouseEvent) => { e.stopPropagation(); layout.openTab(meetingTab(m)); };
  const draftBrief = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!m.workspace_id) { layout.openTab(meetingTab(m)); return; }   // bind a workspace first — prep is where
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
      detail: { prompt: `Draft a one-page prep brief for the meeting "${label(m)}" into ${m.workspace_id}:meetings/${m.id}/prep.md — last touchpoints, open items, attendees worth researching, and a suggested agenda.` },
    }));
  };
  const btn: CSSProperties = { fontSize: 12, fontWeight: 600, padding: "4px 11px", borderRadius: 7, border: "1px solid var(--line2)", background: "transparent", color: "var(--t2)", cursor: "pointer", flex: "none" };
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick}
      style={{ border: "1px solid var(--line2)", background: "var(--panel)", borderRadius: 10,
        padding: "13px 15px", marginBottom: 10, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 650, color: "var(--t1)", flex: 1, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(m)}</span>
        <span style={{ fontSize: 12, color: "var(--t2)", flex: "none" }}>{dayTime(m.scheduled_at)}</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 3 }}>
        {[m.workspace_id ? `workspace ${m.workspace_id}` : "no prep workspace yet",
          armed ? "bot joins automatically" : undefined].filter(Boolean).join(" · ")}
      </div>
      {!hasLink(m) && (
        <div role="alert" style={{ fontSize: 12, color: "var(--warn)", marginTop: 6 }}>
          ⚠ bot not armed — no meeting link
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }} onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
        {brief === false && <button style={{ ...btn, borderColor: "var(--accentbg)", background: "var(--accentbg)", color: "var(--accent)" }} onClick={draftBrief}>Draft brief</button>}
        {brief && <button style={btn} onClick={openPrep}>Open brief</button>}
        {!hasLink(m) && <button style={btn} onClick={openPrep}>Add link</button>}
        <button style={btn} onClick={openPrep}>Prep</button>
      </div>
    </div>
  );
}

// ── THIS WEEK — dense glyph rows, whole row clicks through to prep ────────────────────────────
function Glyph({ ok, name }: { ok: boolean; name: string }) {
  return (
    <span title={`${name} ${ok ? "ready" : "missing"}`}
      style={{ fontFamily: "var(--mono)", fontSize: 11, color: ok ? "var(--t3)" : "var(--warn)", flex: "none", whiteSpace: "nowrap" }}>
      {name} {ok ? "✓" : "–"}
    </span>
  );
}

function WeekRow({ g }: { g: MeetingGroup }) {
  const m = g.current;
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const brief = useBriefExists(m);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick}
      style={{ display: "flex", alignItems: "center", gap: 12, padding: "6px 9px", borderRadius: 7, cursor: "pointer" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <span style={{ fontSize: 13, color: "var(--t1)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(m)}</span>
      <span style={{ fontSize: 12, color: "var(--t3)", flex: "none", width: 92, textAlign: "right" }}>{dayTime(m.scheduled_at)}</span>
      <Glyph ok={!!brief} name="brief" />
      <Glyph ok={botArmed(m)} name="bot" />
      {m.workspace_id && <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", flex: "none" }}>ws ✓</span>}
    </div>
  );
}

// ── TO REVIEW — ended-but-unreviewed recaps; opening one retires it ───────────────────────────
function ReviewLine({ m }: { m: MeetingMock }) {
  const [line, setLine] = useState<string>("…");
  useEffect(() => {
    let on = true;
    fetchDurableTranscript(m.id)
      .then((t) => {
        if (!on) return;
        if (t.notes.length) {
          const chapters = new Set(t.notes.map((n) => n.chapter).filter(Boolean)).size;
          setLine(`${t.notes.length} notes${chapters ? ` · ${chapters} chapter${chapters > 1 ? "s" : ""}` : ""}`);
        } else {
          setLine(t.lines?.length ? `${t.lines.length} transcript lines · not processed yet` : "no transcript captured");
        }
      })
      .catch(() => on && setLine("couldn’t load recording details"));
    return () => { on = false; };
  }, [m.id]);
  const dur = durationLabel(m);
  return <>{[m.when, dur, line].filter(Boolean).join(" · ")}</>;
}

function ReviewRow({ g }: { g: MeetingGroup }) {
  const run = g.pastRuns[0];
  const layout = useService(LayoutServiceId);
  const open = () => { markReviewed(run.id); layout.openTab(meetingTab(run)); };
  return (
    <div onClick={open}
      style={{ display: "flex", flexDirection: "column", gap: 1, padding: "6px 9px", borderRadius: 7, cursor: "pointer" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <span style={{ fontSize: 13, color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(run)}</span>
      <span style={{ fontSize: 11.5, color: "var(--t3)" }}><ReviewLine m={run} /></span>
    </div>
  );
}

// ── the tab ───────────────────────────────────────────────────────────────────────────────────
function TodayView() {
  const meetings = useLiveMeetings();
  const reviewedIds = useReviewed();
  const [showLater, setShowLater] = useState(false);
  const zones = todayZones(groupMeetings(meetings), new Date(), reviewedIds);
  const empty = !zones.now.length && !zones.next && !zones.toReview.length;
  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "18px 22px", maxWidth: 720 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: "var(--t1)" }}>Today</div>
      <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 2 }}>
        What needs you — live now, next up, and recaps waiting.
      </div>
      {empty && (
        <div style={{ marginTop: 22, border: "1px dashed var(--line2)", borderRadius: 10, padding: "16px 18px",
          fontSize: 12.5, color: "var(--t2)", lineHeight: 1.6, display: "flex", gap: 10, alignItems: "flex-start" }}>
          <Icon name="cal" size={16} style={{ color: "var(--t3)", flex: "none", marginTop: 1 }} />
          <span>No meetings yet. Paste a Google Meet link in the sidebar to send the bot,
          plan a meeting, or connect your calendar so scheduled meetings appear here by themselves.</span>
        </div>
      )}
      {zones.now.length > 0 && <Section title="now">{zones.now.map((g) => <NowCard key={g.key} g={g} />)}</Section>}
      {zones.next && <Section title="next"><NextCard g={zones.next} /></Section>}
      {zones.thisWeek.length > 0 && <Section title="this week">{zones.thisWeek.map((g) => <WeekRow key={g.key} g={g} />)}</Section>}
      {zones.later.length > 0 && (
        <>
          <button onClick={() => setShowLater((v) => !v)}
            style={{ display: "block", background: "transparent", border: "none", cursor: "pointer", padding: "10px 9px 0",
              fontFamily: "var(--mono)", fontSize: 11, letterSpacing: ".06em", color: "var(--t3)", textAlign: "left" }}>
            {zones.later.length} more after Sunday {showLater ? "▾" : "▸"}
          </button>
          {showLater && zones.later.map((g) => <WeekRow key={g.key} g={g} />)}
        </>
      )}
      {zones.toReview.length > 0 && <Section title="to review">{zones.toReview.map((g) => <ReviewRow key={g.key} g={g} />)}</Section>}
      <div style={{ fontSize: 11.5, color: "var(--t3)", margin: "26px 9px 10px", lineHeight: 1.5 }}>
        Older meetings live in Knowledge — ask the agent about anything that was said or decided.
      </div>
    </div>
  );
}

registerTab("today", TodayView);
