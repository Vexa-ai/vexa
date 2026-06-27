"use client";
// Failure-surfacing banner for the live meeting feed. The signals (connected / reconnects /
// lastTranscriptAt / issues[]) are already tracked per live meeting and exposed as
// `meeting.diagnostics`; this is the LOUD surface that makes a broken feed unmissable to an operator
// instead of silently showing stale lines.
import { useEffect, useState } from "react";
import { useMeeting } from "./useMeeting";
import { formatElapsed, meetingHealth, STALE_MS, type MeetingHealthKind } from "./meetingHealth";

// Live statuses (mirrors the meeting surface header) — a feed we should watch for staleness/drops.
const LIVE_STATUSES = new Set(["active", "live", "requested", "joining", "awaiting_admission", "needs_help", "stopping"]);

const TONE: Record<Exclude<MeetingHealthKind, "ok">, { color: string; bg: string }> = {
  ended: { color: "var(--t2)", bg: "var(--panel2)" },
  disconnected: { color: "var(--live)", bg: "var(--livebg)" },
  stalled: { color: "var(--live)", bg: "var(--livebg)" },
  error: { color: "var(--live)", bg: "var(--livebg)" },
};

function issueLabel(kind: "stream" | "model" | "parse"): string {
  if (kind === "model") return "Model inference error";
  if (kind === "parse") return "Transcript parse error";
  return "Stream error";
}

export function MeetingHealthBanner() {
  const meeting = useMeeting();
  const diagnostics = meeting.diagnostics;
  const status = String(meeting.meeting.status ?? "").toLowerCase();
  const live = LIVE_STATUSES.has(status);

  // A cheap 1s now-tick so the "no new lines for Ns" elapsed time keeps ticking while stale/disconnected.
  // Only runs while a live feed could go stale; cleaned up on unmount / when not live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);

  const [dismissedAt, setDismissedAt] = useState<number | undefined>(undefined);

  const health = meetingHealth(diagnostics, now, live, STALE_MS);
  if (health.kind === "ok") return null;

  // The error chip (model/parse/stream issue) is dismissible; the headline failure states are not.
  const issue = health.latestIssue;
  const issueDismissed = issue?.at != null && dismissedAt === issue.at;

  // Nothing to show: a fresh, connected, error-free live feed (or a dismissed lone error).
  if (health.kind === "error" && issueDismissed) return null;

  const tone = TONE[health.kind];
  const elapsed = health.staleForMs != null ? formatElapsed(health.staleForMs) : undefined;

  let headline: string;
  let dot = true;
  switch (health.kind) {
    case "ended":
      headline = "Meeting ended";
      dot = false;
      break;
    case "disconnected":
      headline = `Live stream disconnected — reconnecting…${health.reconnects ? ` (${health.reconnects} ${health.reconnects === 1 ? "retry" : "retries"})` : ""}`;
      break;
    case "stalled":
      headline = `Transcript stalled — no new lines${elapsed ? ` for ${elapsed}` : ""}`;
      break;
    case "error":
    default:
      headline = issue ? `${issueLabel(issue.kind)}${issue.status ? ` (${issue.status})` : ""}` : "Meeting feed error";
      break;
  }

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
        <span style={{ fontWeight: 700 }}>{headline}</span>
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
