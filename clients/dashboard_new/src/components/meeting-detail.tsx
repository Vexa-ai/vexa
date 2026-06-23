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
import { Square, AlertTriangle } from "lucide-react";
import { useVexa } from "../app/providers";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const LIVE_STATUSES = new Set(["requested", "joining", "awaiting_admission", "active", "needs_help", "needs_human_help"]);

/** Color-code the status pill by lifecycle phase. */
function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (status === "completed") return "secondary";
  if (LIVE_STATUSES.has(status)) return "default";
  return "outline";
}

/** The truthful connection indicator — reflects the OBSERVED ws state, not intent. */
function ConnectionDot({ connection }: { connection: string }) {
  const map: Record<string, { color: string; label: string }> = {
    live: { color: "var(--chart-2, #10b981)", label: "Live" },
    connecting: { color: "var(--chart-4, #f59e0b)", label: "Connecting" },
    error: { color: "var(--destructive, #ef4444)", label: "Connection error" },
    closed: { color: "var(--muted-foreground)", label: "Closed" },
    idle: { color: "var(--muted-foreground)", label: "Idle" },
  };
  const c = map[connection] ?? map.idle;
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: c.color }} />
      {c.label}
    </span>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function MeetingDetail({ meetingId }: { meetingId: string }) {
  const { apiClient, wsClientFactory, ready } = useVexa();

  const [meeting, setMeeting] = useState<MeetingResponse | null>(null);
  const [snapshot, setSnapshot] = useState<MeetingState | null>(null);
  const [statusHistory, setStatusHistory] = useState<StatusTransition[]>([]);
  const [wsLog, setWsLog] = useState<WsLogEvent[]>([]);
  const [masterSrc, setMasterSrc] = useState<string | undefined>(undefined);
  const [recordingError, setRecordingError] = useState<string | null>(null);
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

      // Resolve the playable recording URL. DF4 — distinguish "no recording yet" (a genuine absence) from
      // a real read FAILURE (auth/server/schema): the former is a normal empty state, the latter is loud.
      try {
        const tr = await apiClient.getTranscripts(m.platform || "", m.native_meeting_id || "");
        const rec = tr.recordings?.[0] as Record<string, unknown> | undefined;
        if (!rec) {
          // no recording exists yet — leave masterSrc undefined (the empty state), not an error.
        } else {
          const rid = (rec.id as number) ?? (rec.recording_id as number) ?? m.id;
          const master = await apiClient.getRecordingMaster(rid);
          if (master?.raw_url) setMasterSrc(`/api/vexa${master.raw_url}`);
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        // A not-found is "no recording yet"; anything else (401/403/500/schema) is a real, visible error.
        if (/\b404\b|not found/i.test(msg)) {
          /* genuine no-recording → empty state */
        } else if (alive) {
          setRecordingError(msg);
        }
      }

      if (!m.platform || !m.native_meeting_id) return;

      store = createMeetingState({
        apiClient,
        wsClientFactory,
        meeting: { platform: m.platform, native_id: m.native_meeting_id, id: m.id },
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

  if (error)
    return (
      <Card className="border-destructive/50">
        <CardContent className="text-destructive flex items-center gap-2 py-6 text-sm">
          <AlertTriangle className="h-4 w-4" /> {error}
        </CardContent>
      </Card>
    );
  if (!meeting) return <div className="text-muted-foreground p-6 text-sm">Loading meeting…</div>;

  const status = snapshot?.status ?? meeting.status;
  const segments = snapshot?.segments ?? [];
  const chat: ChatMessage[] = (snapshot?.chat ?? []).map((c) => ({ sender: c.sender, text: c.text, is_from_bot: true }));
  const isLive = LIVE_STATUSES.has(status);

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
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">{meeting.platform}</h1>
            <p className="text-muted-foreground font-mono text-xs">{meeting.native_meeting_id}</p>
          </div>
          <Badge variant={statusVariant(status)}>{status}</Badge>
          <div className="ml-auto flex items-center gap-3">
            <ConnectionDot connection={snapshot?.connection ?? "idle"} />
            {isLive && (
              <Button variant="destructive" size="sm" onClick={stopBot} disabled={stopping}>
                <Square className="h-3.5 w-3.5" /> {stopping ? "Stopping…" : "Stop bot"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-6">
          <Panel title="Transcript">
            <div className="h-[28rem]">
              <TranscriptViewer
                segments={segments}
                isLive={isLive}
                playbackTime={playbackTime}
                onSegmentClick={(startSeconds) => audioRef.current?.seekTo(toRecordingOffset(startSeconds))}
              />
            </div>
          </Panel>
          <Panel title="Recording">
            {masterSrc ? (
              <AudioPlayer ref={audioRef} src={masterSrc} onTimeUpdate={setPlaybackTime} />
            ) : recordingError ? (
              // DF4 — a recording-read failure is shown, not swallowed as "No recording yet".
              <div className="text-destructive flex items-center gap-2 text-sm">
                <AlertTriangle className="h-4 w-4" /> Couldn&apos;t load the recording: {recordingError}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">No recording yet.</p>
            )}
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel title="Status history">
            <StatusHistory transitions={statusHistory} />
          </Panel>
          <Panel title="Live WS log">
            <WsEventLog events={wsLog} />
          </Panel>
          <Panel title="Chat">
            <ChatPanel messages={chat} isActive={isLive} />
          </Panel>
          {isLive && (
            <Panel title="Bot screen">
              <VncView vncUrl={vncUrl} title="Bot session" />
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
