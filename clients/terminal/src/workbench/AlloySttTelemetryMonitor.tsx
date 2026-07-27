"use client";

import {
  type ReactNode,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import {
  alloySttTelemetryPoller,
  classifyAlloySttMeeting,
  summarizeAlloySttTelemetry,
  type AlloySttMeetingHealth,
  type AlloySttTelemetryPoller,
} from "./alloySttTelemetry";

type AlloySttTelemetryMonitorProps = {
  poller?: AlloySttTelemetryPoller;
  now?: () => number;
  disabledFallback?: ReactNode;
};

const HEALTH_COLORS: Record<AlloySttMeetingHealth, string> = {
  healthy: "#3bb273",
  backlogged: "#c58a23",
  failed: "#d14d57",
  stale: "#c58a23",
};

const formatSeconds = (value: number) => `${value.toFixed(1)}s`;
const formatAge = (ageMs: number) =>
  ageMs < 1_000 ? "now" : `${Math.floor(ageMs / 1_000)}s ago`;

export function AlloySttTelemetryMonitor({
  poller = alloySttTelemetryPoller,
  now = Date.now,
  disabledFallback = null,
}: AlloySttTelemetryMonitorProps) {
  const state = useSyncExternalStore(
    poller.store.subscribe,
    poller.store.getState,
    poller.store.getState,
  );
  const [expanded, setExpanded] = useState(false);
  const nowMs = now();
  const summary = useMemo(
    () => summarizeAlloySttTelemetry(state.meetings, nowMs),
    [state.meetings, nowMs],
  );

  useEffect(() => {
    poller.start();
    return () => poller.stop();
  }, [poller]);

  if (state.fetchedAtMs !== null && !state.enabled) {
    return <>{disabledFallback}</>;
  }

  const unavailable = Boolean(state.transportError) ||
    (state.fetchedAtMs !== null && !state.available);
  const label = state.fetchedAtMs === null
    ? "STT connecting"
    : !state.enabled
      ? "STT monitor off"
      : unavailable
        ? "STT unavailable"
        : summary.meetingCount === 0
          ? "STT idle"
          : [
            `STT ${summary.meetingCount}`,
            `${summary.activeRequests} active`,
            `${summary.waitingChannels} waiting`,
            `${formatSeconds(summary.queuedAudioSec)} queued`,
            `lag ${formatSeconds(summary.maxLagSec)}`,
            `RTF ${summary.maxRtf?.toFixed(2) ?? "n/a"}`,
          ].join(" · ");
  const indicatorColor = unavailable
    ? HEALTH_COLORS.failed
    : state.fetchedAtMs === null || !state.enabled || summary.meetingCount === 0
      ? "var(--text-muted)"
      : HEALTH_COLORS[summary.health];

  return (
    <div style={{ position: "relative", maxWidth: "100%" }}>
      <button
        type="button"
        aria-label="Open STT telemetry details"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        style={{
          border: 0,
          background: "transparent",
          color: indicatorColor,
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 11,
          lineHeight: 1,
          maxWidth: "min(720px, 70vw)",
          overflow: "hidden",
          padding: "4px 8px",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            background: indicatorColor,
            borderRadius: "50%",
            display: "inline-block",
            height: 6,
            marginRight: 6,
            width: 6,
          }}
        />
        {label}
      </button>

      {expanded && (
        <section
          aria-label="STT telemetry details"
          style={{
            background: "var(--surface, #15181d)",
            border: "1px solid var(--border, #30343b)",
            borderRadius: 8,
            bottom: 30,
            boxShadow: "0 12px 30px rgba(0,0,0,.28)",
            color: "var(--text, #e7e9ed)",
            maxHeight: "min(460px, 60vh)",
            minWidth: 420,
            overflow: "auto",
            padding: 12,
            position: "absolute",
            right: 0,
            width: "min(620px, calc(100vw - 32px))",
            zIndex: 100,
          }}
        >
          <header style={{ fontSize: 12, fontWeight: 650, marginBottom: 10 }}>
            ALLOY STT queue
          </header>

          {state.transportError && (
            <div style={{ color: HEALTH_COLORS.failed, marginBottom: 10 }}>
              {state.transportError}
            </div>
          )}

          {!state.transportError && state.meetings.length === 0 && (
            <div style={{ color: "var(--text-muted)" }}>
              No active STT meetings.
            </div>
          )}

          {state.meetings.map((meeting) => {
            const health = classifyAlloySttMeeting(meeting, nowMs);
            return (
              <article
                key={meeting.meeting_id}
                style={{
                  borderTop: "1px solid var(--border, #30343b)",
                  padding: "10px 0",
                }}
              >
                <div style={{ alignItems: "center", display: "flex", gap: 8 }}>
                  <strong style={{ flex: 1 }}>{meeting.native_meeting_id}</strong>
                  <span style={{ color: HEALTH_COLORS[health] }}>{health}</span>
                </div>
                <div
                  style={{
                    color: "var(--text-muted)",
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "4px 12px",
                    marginTop: 6,
                  }}
                >
                  <span>{meeting.active_requests} active</span>
                  <span>{meeting.waiting_channels} waiting</span>
                  <span>{formatSeconds(meeting.queued_audio_sec)} queued</span>
                  <span>lag {formatSeconds(meeting.lag_sec)}</span>
                  <span>RTF {meeting.rtf_ema?.toFixed(2) ?? "n/a"}</span>
                  <span>{meeting.superseded_windows} superseded</span>
                  <span>
                    updated {formatAge(Math.max(0, nowMs - meeting.updated_at_ms))}
                  </span>
                </div>
                {meeting.last_error && (
                  <div style={{ color: HEALTH_COLORS.failed, marginTop: 6 }}>
                    {meeting.last_error.code}: {meeting.last_error.message}
                  </div>
                )}
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
