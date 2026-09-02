"use client";
// THE BOT'S STATE CARD — at the door · admitted · quiet · left. The signals (the meeting's status,
// plus connected / reconnects / lastTranscriptAt / issues[] tracked per live meeting and exposed as
// `meeting.diagnostics`) collapse into one verdict in `meetingHealth`, and the wording lives in one
// pure function, `botStateHeadline`.
//
// It says where the BOT is and nothing else. Every earlier phrasing described the FEED — "Waiting
// for transcript — no new lines for 24s" — which reads as the product working on something; the
// product does no work during a meeting (PRD decision 34), so no wording here may imply it.
import { useEffect, useState } from "react";
import { useMeeting } from "./useMeeting";
import { botStateHeadline, meetingHealth, STALE_MS, type MeetingHealthKind } from "./meetingHealth";

// Live statuses (mirrors the meeting surface header) — a feed we should watch for staleness/drops.
const LIVE_STATUSES = new Set(["active", "live", "requested", "joining", "awaiting_admission", "needs_help", "stopping"]);

// Only a real error is LOUD (red). A bot at the door, a reconnect, or a quiet room is benign —
// often just silence, not a failure — so those get a muted, informational tone.
const TONE: Record<Exclude<MeetingHealthKind, "ok">, { color: string; bg: string }> = {
  "at-door": { color: "var(--t2)", bg: "var(--panel2)" },
  ended: { color: "var(--t2)", bg: "var(--panel2)" },
  disconnected: { color: "var(--t2)", bg: "var(--panel2)" },
  stalled: { color: "var(--t2)", bg: "var(--panel2)" },
  // Warm amber (brand accent) — noticeable but not the alarming blood-red of a fatal failure.
  error: { color: "var(--accent)", bg: "var(--accentbg)" },
};

// The two things that can go wrong on the wire between the bot and this pane. Neither is a model:
// one is the connection, the other is a frame that would not decode.
function issueLabel(kind: "stream" | "parse"): string {
  return kind === "parse" ? "Unreadable transcript frame" : "Bot feed error";
}

export function MeetingHealthBanner() {
  const meeting = useMeeting();
  const diagnostics = meeting.diagnostics;
  const status = String(meeting.meeting.status ?? "").toLowerCase();
  const live = LIVE_STATUSES.has(status);

  // A cheap 1s now-tick so the "no words for Ns" elapsed time keeps ticking while quiet/disconnected.
  // Only runs while a live feed could go stale; cleaned up on unmount / when not live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);

  const [dismissedAt, setDismissedAt] = useState<number | undefined>(undefined);

  const health = meetingHealth(diagnostics, now, live, STALE_MS, status);
  if (health.kind === "ok") return null;

  // The error chip (a wire issue) is dismissible; the headline bot states are not.
  const issue = health.latestIssue;
  const issueDismissed = issue?.at != null && dismissedAt === issue.at;

  // Nothing to show: a fresh, connected, error-free live feed (or a dismissed lone error).
  if (health.kind === "error" && issueDismissed) return null;

  const tone = TONE[health.kind];
  // ONE source for the words (meetingHealth.botStateHeadline); the error state is the only one whose
  // line depends on the issue itself, so it is composed here from the same label the detail row uses.
  const headline = health.kind === "error" && issue
    ? `${issueLabel(issue.kind)}${issue.status ? ` (${issue.status})` : ""}`
    : botStateHeadline(health);
  const dot = health.kind !== "ended";   // the bot has left — nothing is pulsing

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: "flex", flexDirection: "column", gap: 4,
        margin: "8px 18px 0", padding: "8px 11px", borderRadius: 8,
        background: tone.bg, border: `1px solid ${tone.color}`, color: tone.color,
        fontSize: 12.5, lineHeight: 1.4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: tone.color, flex: "none", boxShadow: `0 0 0 3px ${tone.bg}` }} />}
        <span style={{ fontWeight: health.kind === "error" ? 700 : 600 }}>{headline}</span>
      </div>

      {/* For stalled/disconnected, still surface the most recent underlying issue if there is one. */}
      {health.kind !== "ended" && health.kind !== "error" && issue && !issueDismissed && (
        <div style={{ fontSize: 11.5, opacity: 0.92 }}>
          {issueLabel(issue.kind)}{issue.status ? ` (${issue.status})` : ""}: {issue.message}
        </div>
      )}

      {/* The error chip carries the message + a dismiss control. */}
      {health.kind === "error" && issue && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11.5, opacity: 0.92, flex: 1, minWidth: 0 }}>{issue.message}</span>
          <button
            type="button"
            onClick={() => setDismissedAt(issue.at)}
            title="Dismiss"
            style={{ flex: "none", background: "transparent", border: `1px solid ${tone.color}`, color: tone.color, borderRadius: 6, padding: "1px 7px", fontSize: 11, cursor: "pointer", fontWeight: 600 }}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
