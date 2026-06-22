"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createMeetingState, type MeetingState, type MeetingStateStore } from "@vexa/dash-meeting-state";
import type { MeetingResponse } from "@vexa/dash-contracts";
import { TranscriptViewer } from "@vexa/dash-transcript-viewer";
import { AudioPlayer, type AudioPlayerHandle } from "@vexa/dash-recording-players";
import { StatusHistory, type StatusTransition } from "@vexa/dash-status-history";
import { WsEventLog, type WsLogEvent } from "@vexa/dash-ws-event-log";
import { ChatPanel, type ChatMessage } from "@vexa/dash-chat";
import { VncView } from "@vexa/dash-vnc-view";
import { useVexa } from "../app/providers";

const LIVE_STATUSES = new Set(["requested", "joining", "awaiting_admission", "active", "needs_help", "needs_human_help"]);

/**
 * The meeting-detail composite — the live happy-path screen.
 *
 * It is the ONLY place the bricks meet: `@vexa/dash-meeting-state` is the single source of truth (it
 * merges REST seed + the 0.10.6 WS stream behind the injected ports), and every view brick is a pure
 * projection of its snapshot — TranscriptViewer over `segments`, ChatPanel over `chat`, StatusHistory
 * + WsEventLog over the observed status/chat/segment deltas, AudioPlayer over the recording master,
 * VncView over the per-bot route. No brick fetches or sockets on its own; this composite wires them.
 */
export function MeetingDetail({ meetingId }: { meetingId: string }) {
  const { apiClient, wsClientFactory, ready } = useVexa();

  const [meeting, setMeeting] = useState<MeetingResponse | null>(null);
  const [snapshot, setSnapshot] = useState<MeetingState | null>(null);
  const [statusHistory, setStatusHistory] = useState<StatusTransition[]>([]);
  const [wsLog, setWsLog] = useState<WsLogEvent[]>([]);
  const [masterSrc, setMasterSrc] = useState<string | undefined>(undefined);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);

  const audioRef = useRef<AudioPlayerHandle>(null);
  const lastStatus = useRef<string | null>(null);
  const lastSegCount = useRef(0);
  const lastChatCount = useRef(0);

  const pushLog = useCallback((type: string, summary: string) => {
    setWsLog((prev) => [...prev, { ts: new Date().toLocaleTimeString(), type, summary }]);
  }, []);

  const onSnapshot = useCallback(
    (s: MeetingState) => {
      setSnapshot(s);

      if (s.status !== lastStatus.current) {
        const from = lastStatus.current;
        lastStatus.current = s.status;
        setStatusHistory((prev) => [...prev, { from: from ?? undefined, to: s.status, timestamp: new Date().toISOString() }]);
        pushLog("meeting.status", `status: ${s.status}`);
      }
      if (s.segments.length !== lastSegCount.current) {
        const added = s.segments.length - lastSegCount.current;
        lastSegCount.current = s.segments.length;
        if (added > 0) pushLog("transcript", `+${added} segment${added === 1 ? "" : "s"} (${s.segments.length} total)`);
      }
      if (s.chat.length !== lastChatCount.current) {
        const newest = s.chat[s.chat.length - 1];
        lastChatCount.current = s.chat.length;
        if (newest) pushLog("chat_message", `${newest.sender ?? "bot"}: ${newest.text}`);
      }
    },
    [pushLog]
  );

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    let store: MeetingStateStore | null = null;
    let unsub: (() => void) | undefined;

    (async () => {
      const m = await apiClient.getMeeting(meetingId);
      if (!alive) return;
      setMeeting(m);
      lastStatus.current = m.status;
      setStatusHistory([{ to: m.status, timestamp: m.start_time || m.created_at }]);

      // Resolve the playable recording URL: the meeting's first recording → GET /recordings/{id}/master
      // returns a DESCRIPTOR whose `raw_url` is the actual audio-bytes path (proxied for auth + Range).
      try {
        const tr = await apiClient.getTranscripts(m.platform || "", m.native_meeting_id || "");
        const rec = tr.recordings?.[0] as Record<string, unknown> | undefined;
        const rid = (rec?.id as number) ?? (rec?.recording_id as number) ?? m.id;
        const master = await apiClient.getRecordingMaster(rid);
        if (master?.raw_url) setMasterSrc(`/api/vexa${master.raw_url}`);
      } catch {
        /* no recording yet — the player section shows "No recording yet." */
      }

      if (!m.platform || !m.native_meeting_id) return; // can't open a live stream without the handle

      store = createMeetingState({
        apiClient,
        wsClientFactory,
        meeting: { platform: m.platform, native_id: m.native_meeting_id, id: m.id },
        // seed the store with the REST status so a terminal/reopened meeting shows its real status
        // immediately (live meeting.status frames still override it).
        initialStatus: m.status,
      });
      unsub = store.subscribe(onSnapshot);
      setSnapshot(store.getState());
      await store.bootstrap();
      if (!alive) return;
      store.connectLive();
    })().catch((e: unknown) => {
      if (alive) setError(e instanceof Error ? e.message : String(e));
    });

    return () => {
      alive = false;
      unsub?.();
      store?.stop();
    };
  }, [ready, meetingId, apiClient, wsClientFactory, onSnapshot]);

  const stopBot = useCallback(async () => {
    if (!meeting?.platform || !meeting?.native_meeting_id) return;
    setStopping(true);
    try {
      await apiClient.deleteBot(meeting.platform, meeting.native_meeting_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStopping(false);
    }
  }, [apiClient, meeting]);

  if (error) return <div className="panel" style={{ color: "var(--bad)" }}>Error: {error}</div>;
  if (!meeting) return <div className="panel muted">Loading meeting…</div>;

  const status = snapshot?.status ?? meeting.status;
  const segments = snapshot?.segments ?? [];
  const chat: ChatMessage[] = (snapshot?.chat ?? []).map((c) => ({ sender: c.sender, text: c.text, is_from_bot: true }));
  const isLive = LIVE_STATUSES.has(status);

  // Segment times can arrive as an ABSOLUTE unix epoch (the REST `start`), but the recording timeline
  // is relative (0..duration). Map a clicked segment's start to a recording offset by anchoring on the
  // EARLIEST segment (the recording's effective start) — robust even when meeting.start_time predates
  // the audio. Already-relative values (< ~1e9) pass through unchanged.
  const segStarts = segments
    .map((s) => (typeof s.start_time === "number" ? s.start_time : parseFloat(String(s.start_time))))
    .filter((n) => Number.isFinite(n) && n > 0);
  const recAnchor = segStarts.length ? Math.min(...segStarts) : 0;
  const toRecordingOffset = (startSeconds: number) =>
    Math.max(0, startSeconds > 1_000_000_000 && recAnchor ? startSeconds - recAnchor : startSeconds);

  const botId = meeting.bot_container_id;
  const vncUrl =
    botId && isLive
      ? `/b/${botId}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/${botId}/vnc/websockify`
      : "";

  return (
    <div>
      <div className="panel" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h2 style={{ margin: 0 }}>
          {meeting.platform} · {meeting.native_meeting_id}
        </h2>
        <span className="status-pill" data-status={status}>{status}</span>
        <span className="muted" style={{ marginLeft: "auto" }}>connection: {snapshot?.connection ?? "idle"}</span>
        {isLive && (
          <button className="btn danger" onClick={stopBot} disabled={stopping}>
            {stopping ? "Stopping…" : "Stop bot"}
          </button>
        )}
      </div>

      <div className="detail-grid">
        <div>
          <div className="panel">
            <h2>Transcript</h2>
            <TranscriptViewer
              segments={segments}
              isLive={isLive}
              playbackTime={playbackTime}
              onSegmentClick={(startSeconds) => audioRef.current?.seekTo(toRecordingOffset(startSeconds))}
            />
          </div>
          <div className="panel">
            <h2>Recording</h2>
            {masterSrc ? (
              <AudioPlayer ref={audioRef} src={masterSrc} onTimeUpdate={setPlaybackTime} />
            ) : (
              <p className="muted">No recording yet.</p>
            )}
          </div>
        </div>

        <div>
          <div className="panel">
            <h2>Status history</h2>
            <StatusHistory transitions={statusHistory} />
          </div>
          <div className="panel">
            <h2>Live WS log</h2>
            <WsEventLog events={wsLog} />
          </div>
          <div className="panel">
            <h2>Chat</h2>
            <ChatPanel messages={chat} isActive={isLive} />
          </div>
          {isLive && (
            <div className="panel">
              <h2>Bot screen</h2>
              <VncView vncUrl={vncUrl} title="Bot session" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
