"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import type { MeetingStatus } from "@/types/vexa";

interface WsEvent {
  direction: "out" | "in";
  type: string;
  payload: Record<string, unknown>;
  ts: string; // HH:MM:SS
}

interface WsEventLogProps {
  status: MeetingStatus;
  platform: string;
  nativeId: string;
  wsConnected: boolean;
  wsConnecting: boolean;
  /** Transcript segment count — used to show transcript events */
  segmentCount: number;
}

// Build the event list from current state
function buildEvents(
  status: MeetingStatus,
  platform: string,
  nativeId: string,
  wsConnected: boolean,
  wsConnecting: boolean,
  segmentCount: number,
): WsEvent[] {
  const events: WsEvent[] = [];
  const now = new Date();
  const ts = (offsetSec: number) => {
    const d = new Date(now.getTime() - offsetSec * 1000);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const STATUS_ORDER: Record<string, number> = {
    requested: 0,
    joining: 1,
    awaiting_admission: 2,
    active: 3,
    completed: 4,
    stopping: 4,
    failed: -1,
  };

  const currentStep = STATUS_ORDER[status] ?? -1;

  // 1. Connection
  if (wsConnected || wsConnecting || currentStep >= 0) {
    events.push({
      direction: "out",
      type: "connect",
      payload: { url: "wss://api.vexa.ai/ws" },
      ts: ts(30),
    });
  }

  // 2. Subscribe
  if (wsConnected || currentStep >= 0) {
    events.push({
      direction: "out",
      type: "subscribe",
      payload: { meetings: [{ platform, native_id: nativeId }] },
      ts: ts(29),
    });

    events.push({
      direction: "in",
      type: "subscribed",
      payload: { meetings: ["..."] },
      ts: ts(28),
    });
  }

  // 3. Status events — show each stage that has been reached
  if (currentStep >= 0) {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "requested" },
      ts: ts(25),
    });
  }

  if (currentStep >= 1) {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "joining" },
      ts: ts(20),
    });
  }

  if (currentStep >= 2) {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "awaiting_admission" },
      ts: ts(15),
    });
  }

  if (currentStep >= 3) {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "active" },
      ts: ts(10),
    });
  }

  // 4. Transcript events when active
  if (currentStep >= 3 && segmentCount > 0) {
    events.push({
      direction: "in",
      type: "transcript.mutable",
      payload: {
        segments: [
          {
            speaker: "...",
            text: "...",
            absolute_start_time: "...",
          },
        ],
      },
      ts: ts(5),
    });

    if (segmentCount > 1) {
      events.push({
        direction: "in",
        type: "transcript.mutable",
        payload: {
          segments: [`... ${segmentCount} segments total`],
        },
        ts: ts(2),
      });
    }
  }

  // 5. Terminal states
  if (status === "completed" || status === "stopping") {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "completed" },
      ts: ts(1),
    });
  }

  if (status === "failed") {
    events.push({
      direction: "in",
      type: "meeting.status",
      payload: { status: "failed" },
      ts: ts(1),
    });
  }

  return events;
}

// Syntax-highlight JSON value
function JsonValue({ value }: { value: unknown }) {
  if (typeof value === "string") {
    return <span className="text-[#6ee7b7]">&quot;{value}&quot;</span>;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="text-[#fca5a5]">{String(value)}</span>;
  }
  if (value === null) {
    return <span className="text-gray-500">null</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-500">[]</span>;
    return (
      <span>
        <span className="text-gray-500">[</span>
        {value.map((item, i) => (
          <span key={i}>
            {i > 0 && <span className="text-gray-600">, </span>}
            <JsonValue value={item} />
          </span>
        ))}
        <span className="text-gray-500">]</span>
      </span>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <span>
        <span className="text-gray-500">{"{"}</span>
        {entries.map(([k, v], i) => (
          <span key={k}>
            {i > 0 && <span className="text-gray-600">, </span>}
            <span className="text-[#7dd3fc]">{k}</span>
            <span className="text-gray-600">: </span>
            <JsonValue value={v} />
          </span>
        ))}
        <span className="text-gray-500">{"}"}</span>
      </span>
    );
  }
  return <span className="text-gray-400">{String(value)}</span>;
}

// Color for event type
function getTypeColor(type: string): string {
  if (type === "connect" || type === "subscribe") return "text-[#7dd3fc]";
  if (type === "subscribed" || type === "pong") return "text-gray-500";
  if (type === "meeting.status") return "text-[#c4b5fd]";
  if (type.startsWith("transcript")) return "text-[#6ee7b7]";
  if (type === "error") return "text-[#fca5a5]";
  return "text-gray-400";
}

// Color for status value in meeting.status events
function getStatusColor(status: string): string {
  if (status === "active") return "text-[#6ee7b7]";
  if (status === "completed") return "text-[#7dd3fc]";
  if (status === "failed") return "text-[#fca5a5]";
  if (status === "awaiting_admission") return "text-[#fbbf24]";
  return "text-[#c4b5fd]";
}

// Mask token for display: show first 8 chars + "..."
function maskToken(token: string): string {
  if (token.length <= 12) return token.slice(0, 4) + "...";
  return token.slice(0, 8) + "...";
}

// Copy button component for terminal cards
function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback
    }
  };

  return (
    <button
      onClick={handleCopy}
      className={`p-1 rounded hover:bg-gray-700/50 transition-colors ${className || ""}`}
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-emerald-400" />
      ) : (
        <Copy className="h-3.5 w-3.5 text-gray-500 hover:text-gray-300" />
      )}
    </button>
  );
}

export function WsEventLog({
  status,
  platform,
  nativeId,
  wsConnected,
  wsConnecting,
  segmentCount,
}: WsEventLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const events = buildEvents(status, platform, nativeId, wsConnected, wsConnecting, segmentCount);

  // Auto-scroll to bottom when events change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  const isLive = status === "requested" || status === "joining" || status === "awaiting_admission" || status === "active";

  return (
    <div className="rounded-[16px] border border-border overflow-hidden shadow-lg bg-[#111111]">
      {/* Chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#1a1a1a]">
        <div className="flex items-center gap-[6px]">
          <span className="w-[11px] h-[11px] rounded-full bg-[#ff5f57]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#febc2e]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#28c840]" />
        </div>
        <span className="text-[11px] text-gray-500 font-mono">
          WebSocket · api.vexa.ai/ws
        </span>
        {isLive && (
          <div className="flex items-center gap-1.5">
            <span className="w-[7px] h-[7px] rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-[10px] text-gray-500 font-mono tracking-widest font-semibold">
              LIVE
            </span>
          </div>
        )}
        {!isLive && (
          <span className="text-[10px] text-gray-600 font-mono">
            {status === "completed" ? "DONE" : status.toUpperCase()}
          </span>
        )}
      </div>

      {/* Event stream */}
      <div
        ref={scrollRef}
        className="p-4 font-mono text-[11px] leading-[1.8] max-h-[400px] overflow-y-auto"
      >
        {events.length === 0 && (
          <div className="text-gray-600">
            # Waiting for connection...
          </div>
        )}

        {events.map((event, i) => (
          <div key={`${event.type}-${i}`} className="flex gap-2">
            {/* Direction arrow */}
            <span className={event.direction === "out" ? "text-[#7dd3fc]" : "text-[#6ee7b7]"}>
              {event.direction === "out" ? "→" : "←"}
            </span>

            {/* Timestamp */}
            <span className="text-gray-600 shrink-0">{event.ts}</span>

            {/* Event content */}
            <div className="min-w-0">
              <span className={getTypeColor(event.type)}>{event.type}</span>
              {event.type === "connect" ? (
                <span className="text-gray-500 ml-2">
                  {event.payload.url as string}
                </span>
              ) : event.type === "meeting.status" ? (
                <span className="ml-2">
                  <span className="text-gray-600">{"{ "}</span>
                  <span className="text-gray-400">status</span>
                  <span className="text-gray-600">: </span>
                  <span className={getStatusColor(event.payload.status as string)}>
                    &quot;{event.payload.status as string}&quot;
                  </span>
                  <span className="text-gray-600">{" }"}</span>
                </span>
              ) : (
                <span className="ml-2 text-gray-500 break-all">
                  <JsonValue value={event.payload} />
                </span>
              )}
            </div>
          </div>
        ))}

        {/* Blinking cursor */}
        {isLive && (
          <div className="flex gap-2 mt-1">
            <span className="text-gray-600">←</span>
            <span className="text-gray-600">
              {wsConnecting ? "connecting..." : "listening..."}
            </span>
            <span className="text-gray-400 animate-pulse">▌</span>
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-[#161616] border-t border-gray-800/50 font-mono">
        <div className="flex items-center gap-1.5">
          <span className={`w-[6px] h-[6px] rounded-full ${wsConnected ? "bg-emerald-400" : wsConnecting ? "bg-yellow-400 animate-pulse" : "bg-gray-600"}`} />
          <span className="text-[10px] text-gray-600">
            {wsConnected ? "Connected" : wsConnecting ? "Connecting" : "Disconnected"}
          </span>
        </div>
        <span className="text-[10px] text-gray-700">
          {events.length} events
        </span>
      </div>
    </div>
  );
}

interface RestTranscriptsPreviewProps {
  platform: string;
  nativeId: string;
  segmentCount: number;
  token?: string | null;
}

export function RestTranscriptsPreview({
  platform,
  nativeId,
  token,
}: RestTranscriptsPreviewProps) {
  const displayToken = token ? maskToken(token) : "vx_sk_...";
  const copyToken = token || "YOUR_API_KEY";
  const curlCommand = `curl https://api.vexa.ai/transcripts/${platform}/${nativeId} \\\n  -H 'X-API-Key: ${copyToken}'`;

  return (
    <div className="rounded-[16px] border border-border overflow-hidden shadow-lg bg-[#111111]">
      {/* Chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#1a1a1a]">
        <div className="flex items-center gap-[6px]">
          <span className="w-[11px] h-[11px] rounded-full bg-[#ff5f57]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#febc2e]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#28c840]" />
        </div>
        <span className="text-[11px] text-gray-500 font-mono">
          GET /transcripts
        </span>
        <CopyButton text={curlCommand} />
      </div>

      {/* Content */}
      <div className="p-4 font-mono text-[11px] leading-[1.8]">
        <div className="text-gray-500 mb-2">
          # Get transcripts for this meeting
        </div>
        <div>
          <span className="text-gray-300">curl </span>
          <span className="text-[#6ee7b7]">
            https://api.vexa.ai/transcripts/{platform}/{nativeId}
          </span>
          <span className="text-gray-300"> \</span>
        </div>
        <div className="pl-4">
          <span className="text-gray-300">-H </span>
          <span className="text-[#7dd3fc]">&apos;X-API-Key: {displayToken}&apos;</span>
        </div>
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-[#161616] border-t border-gray-800/50 font-mono">
        <div className="flex items-center gap-1.5">
          <span className="w-[6px] h-[6px] rounded-full bg-emerald-400" />
          <span className="text-[10px] text-gray-600">
            REST API · api.vexa.ai
          </span>
        </div>
        <span className="text-[10px] text-gray-700">
          GET /transcripts/{platform}/{nativeId}
        </span>
      </div>
    </div>
  );
}

interface RestRecordingsPreviewProps {
  platform: string;
  nativeId: string;
  token?: string | null;
}

export function RestRecordingsPreview({
  token,
}: RestRecordingsPreviewProps) {
  const displayToken = token ? maskToken(token) : "vx_sk_...";
  const copyToken = token || "YOUR_API_KEY";
  const curlListCommand = `curl https://api.vexa.ai/recordings \\\n  -H 'X-API-Key: ${copyToken}'`;
  const curlDownloadCommand = `curl -L https://api.vexa.ai/recordings/{id}/media/{media_id}/raw \\\n  -H 'X-API-Key: ${copyToken}' \\\n  -o recording.wav`;
  const fullCopy = `${curlListCommand}\n\n${curlDownloadCommand}`;

  return (
    <div className="rounded-[16px] border border-border overflow-hidden shadow-lg bg-[#111111]">
      {/* Chrome bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#1a1a1a]">
        <div className="flex items-center gap-[6px]">
          <span className="w-[11px] h-[11px] rounded-full bg-[#ff5f57]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#febc2e]" />
          <span className="w-[11px] h-[11px] rounded-full bg-[#28c840]" />
        </div>
        <span className="text-[11px] text-gray-500 font-mono">
          GET /recordings
        </span>
        <CopyButton text={fullCopy} />
      </div>

      {/* Content */}
      <div className="p-4 font-mono text-[11px] leading-[1.8]">
        <div className="text-gray-500 mb-2">
          # Get meeting recordings
        </div>
        <div>
          <span className="text-gray-300">curl </span>
          <span className="text-[#6ee7b7]">
            https://api.vexa.ai/recordings
          </span>
          <span className="text-gray-300"> \</span>
        </div>
        <div className="pl-4">
          <span className="text-gray-300">-H </span>
          <span className="text-[#7dd3fc]">&apos;X-API-Key: {displayToken}&apos;</span>
        </div>

        <div className="border-t border-gray-800/50 mt-3 pt-3">
          <div className="text-gray-500 mb-2">
            # Download audio file
          </div>
          <div>
            <span className="text-gray-300">curl -L </span>
            <span className="text-[#6ee7b7]">
              https://api.vexa.ai/recordings/{'{'}<span className="text-[#fca5a5]">id</span>{'}'}/media/{'{'}<span className="text-[#fca5a5]">media_id</span>{'}'}/raw
            </span>
            <span className="text-gray-300"> \</span>
          </div>
          <div className="pl-4">
            <span className="text-gray-300">-H </span>
            <span className="text-[#7dd3fc]">&apos;X-API-Key: {displayToken}&apos;</span>
            <span className="text-gray-300"> \</span>
          </div>
          <div className="pl-4">
            <span className="text-gray-300">-o </span>
            <span className="text-[#6ee7b7]">recording.wav</span>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-1.5 bg-[#161616] border-t border-gray-800/50 font-mono">
        <div className="flex items-center gap-1.5">
          <span className="w-[6px] h-[6px] rounded-full bg-emerald-400" />
          <span className="text-[10px] text-gray-600">
            REST API · api.vexa.ai
          </span>
        </div>
        <span className="text-[10px] text-gray-700">
          GET /recordings
        </span>
      </div>
    </div>
  );
}
