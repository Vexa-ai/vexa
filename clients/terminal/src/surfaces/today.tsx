"use client";
/** Today — the Meetings surface's CENTER tab (design-spec meeting-lifecycle-v2, W2): clicking
 *  "Meetings" in the switcher opens the user's day, not a blank center. Three buckets over the
 *  same live store the sidebar renders from (`useLiveMeetings`):
 *
 *    NOW      — the live bucket (bot in/heading-to the room), green, never red.
 *    UPCOMING — planned meetings (intent statuses) by scheduled time, unscheduled last.
 *    RECENT   — ended runs with something to show, newest first, capped.
 *
 *  Every card routes through the SAME per-state tab logic the sidebar uses (`meetingTab`), so a
 *  planned card opens prep and a recorded card opens the meeting view. Recent cards lazily pull
 *  their durable transcript for an honest "what we have" line (notes/chapters when processed,
 *  "not processed yet" otherwise — decision/action counts are a backend enrichment follow-up). */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { registerTab } from "../contributions";
import { Icon } from "../ui-kit";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { usePreviewPinTab } from "./previewPinTab";
import { meetingPhase, type MeetingMock } from "./meetingModel";
import { useLiveMeetings, fetchDurableTranscript } from "./liveMeetings";
import { meetingTab } from "./meeting";

export interface TodayBuckets {
  now: MeetingMock[];
  upcoming: MeetingMock[];
  recent: MeetingMock[];
}

const RECENT_CAP = 8;

/** Pure bucketing so the arrangement is unit-tested offline. `now` (the clock) is unused today —
 *  buckets derive from lifecycle phase, not wall time — but stays in the signature so a later
 *  "today vs later this week" split doesn't change callers. */
export function todayBuckets(meetings: MeetingMock[], _now: Date = new Date()): TodayBuckets {
  const now: MeetingMock[] = [];
  const upcoming: MeetingMock[] = [];
  const recent: MeetingMock[] = [];
  for (const m of meetings) {
    const phase = meetingPhase(m);
    if (phase === "live") now.push(m);
    else if (phase === "prep") upcoming.push(m);
    else if (m.has_recording || m.start_time) recent.push(m);
  }
  // planned: soonest first, no-time-set last (they need attention but can't be ordered)
  upcoming.sort((a, b) => {
    if (!a.scheduled_at && !b.scheduled_at) return 0;
    if (!a.scheduled_at) return 1;
    if (!b.scheduled_at) return -1;
    return a.scheduled_at.localeCompare(b.scheduled_at);
  });
  // recorded: newest run first
  recent.sort((a, b) => (b.start_time ?? "").localeCompare(a.start_time ?? ""));
  return { now, upcoming, recent: recent.slice(0, RECENT_CAP) };
}

const label = (m: MeetingMock) => m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "");

function durationLabel(m: MeetingMock): string {
  if (!m.start_time || !m.end_time) return "";
  const ms = new Date(m.end_time).getTime() - new Date(m.start_time).getTime();
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const min = Math.round(ms / 60000);
  return min >= 60 ? `${Math.floor(min / 60)}h ${min % 60}m` : `${min} min`;
}

/** The honest recap line: what the pipeline actually holds for this run. */
function RecentStats({ m }: { m: MeetingMock }) {
  const [line, setLine] = useState<string>("…");
  useEffect(() => {
    let on = true;
    fetchDurableTranscript(m.id)
      .then((t) => {
        if (!on) return;
        const notes = t.notes;
        if (notes.length) {
          const chapters = new Set(notes.map((n) => n.chapter).filter(Boolean)).size;
          setLine(`${notes.length} notes${chapters ? ` · ${chapters} chapter${chapters > 1 ? "s" : ""}` : ""}`);
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

const pill = (color: string, bg: string): CSSProperties => ({
  flex: "none", display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--mono)",
  fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase", color, background: bg,
  borderRadius: 999, padding: "2px 9px",
});

function TodayCard({ m, sub, cta }: { m: MeetingMock; sub: ReactNode; cta: string }) {
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const layout = useService(LayoutServiceId);
  const phase = meetingPhase(m);
  const live = phase === "live";
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick}
      style={{ border: `1px solid ${live ? "var(--green)" : "var(--line)"}`, background: "var(--panel)",
        borderRadius: 10, padding: "11px 14px", marginBottom: 10, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--t1)", flex: 1, minWidth: 120,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label(m)}</span>
        {live && <span style={pill("var(--green)", "var(--greenbg)")}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)" }} />live</span>}
        {phase === "prep" && <span style={pill("var(--accent)", "var(--accentbg)")}>{m.scheduled_at ? "scheduled" : "planned"}</span>}
        {phase === "post" && <span style={pill("var(--t3)", "var(--panel2)")}>recorded</span>}
        {m.shared && <span style={{ flex: "none", fontSize: 9.5, color: "var(--t3)", border: "1px solid var(--line)", borderRadius: 5, padding: "0 5px" }}>shared</span>}
        <button onClick={(e) => { e.stopPropagation(); layout.openTab(meetingTab(m)); }}
          style={{ flex: "none", fontSize: 11.5, padding: "3px 10px", borderRadius: 7, border: "none", cursor: "pointer",
            background: live ? "var(--greenbg)" : "var(--accentbg)", color: live ? "var(--green)" : "var(--accent)", fontWeight: 600 }}>
          {cta}
        </button>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--t2)", marginTop: 4 }}>{sub}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase",
        color: "var(--t3)", margin: "18px 0 8px" }}>{title}</div>
      {children}
    </>
  );
}

function TodayView() {
  const meetings = useLiveMeetings();
  const { now, upcoming, recent } = todayBuckets(meetings);
  const empty = !now.length && !upcoming.length && !recent.length;
  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "18px 22px", maxWidth: 720 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: "var(--t1)" }}>Today</div>
      <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 2 }}>
        Your meetings — live now, coming up, and what was captured.
      </div>
      {empty && (
        <div style={{ marginTop: 22, border: "1px dashed var(--line2)", borderRadius: 10, padding: "16px 18px",
          fontSize: 12.5, color: "var(--t2)", lineHeight: 1.6, display: "flex", gap: 10, alignItems: "flex-start" }}>
          <Icon name="cal" size={16} style={{ color: "var(--t3)", flex: "none", marginTop: 1 }} />
          <span>No meetings yet. Paste a Google Meet link in the sidebar to send the bot,
          plan a meeting, or connect your calendar so scheduled meetings appear here by themselves.</span>
        </div>
      )}
      {now.length > 0 && <Section title="now">{now.map((m) => (
        <TodayCard key={m.id} m={m} cta="Open meeting"
          sub={m.live_status === "active" ? "The bot is in the room — transcript is streaming." : `Bot status: ${m.live_status ?? "on its way"}.`} />
      ))}</Section>}
      {upcoming.length > 0 && <Section title="upcoming">{upcoming.map((m) => (
        <TodayCard key={m.id} m={m} cta="Prep"
          sub={[m.when, m.workspace_id ? `workspace ${m.workspace_id}` : "no prep workspace yet",
            m.auto_join !== false && m.meeting_url ? "bot joins automatically" : undefined].filter(Boolean).join(" · ")} />
      ))}</Section>}
      {recent.length > 0 && <Section title="recent">{recent.map((m) => (
        <TodayCard key={m.id} m={m} cta="Recap" sub={<RecentStats m={m} />} />
      ))}</Section>}
    </div>
  );
}

registerTab("today", TodayView);
