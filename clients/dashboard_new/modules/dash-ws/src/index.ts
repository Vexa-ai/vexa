/**
 * @vexa/dash-ws — the UNIFIED dashboard WS client.
 *
 * One front door over the 0.10.6 `/ws` multiplex (core/gateway/contracts/ws.v1). This brick replaces
 * the vendored dashboard's TWO drifted consumers — `use-vexa-websocket` and `use-live-transcripts` —
 * which had forked subscribe/ping/dispatch logic. Here there is ONE dispatch table.
 *
 * Lifecycle (all on an injected `WsTransport`, so no browser globals leak in):
 *   • connect → `wsUrl?api_key=<authToken>`
 *   • on open → send `{action:"subscribe", meetings:[{platform, native_id}]}`, start the 25s ping loop
 *   • on each inbound frame → look up `frame.type` in the dispatch table and fan to the right callback
 *   • on close → stop pinging
 *
 * The gateway forwards redis payloads verbatim, so frames are type-tagged and additive. Unknown types
 * (and `subscribed`/`pong`) are no-ops. `meeting.status` is normalized into the dashboard's own
 * vocabulary (`needs_help → needs_human_help`, per ADR-0023) before it reaches `onStatus`.
 */
import type {
  Platform,
  MeetingStatus,
  TranscriptSegment,
  WsFrame,
  MeetingStatusFrame,
  TranscriptFrame,
  TranscriptionSegmentFrame,
  ChatMessageFrame,
  ErrorFrame,
} from "@vexa/dash-contracts";
import type { WsTransport } from "./ports.js";

export type { WsTransport } from "./ports.js";

/** Keepalive cadence — matches both legacy consumers (25s). */
export const PING_INTERVAL_MS = 25_000;

/** What `onTranscript` receives: either a per-speaker bundle, or a single live segment wrapped in a 1-array. */
export interface TranscriptUpdate {
  /** present for `transcript` bundles */
  speaker?: string | null;
  /** present for `transcript` bundles */
  confirmed?: TranscriptSegment[];
  /** present for `transcript` bundles */
  pending?: TranscriptSegment[];
  /** present for a single `transcription_segment` frame (wrapped so consumers have one shape) */
  segments?: TranscriptSegment[];
}

export interface CreateWsClientOptions {
  /** The socket boundary (real WebSocket wrapper in the browser, fake in tests). */
  transport: WsTransport;
  /** Base WS URL, e.g. `wss://host/ws`. `?api_key=<authToken>` is appended. */
  wsUrl: string;
  /** The api key, sent as the `api_key` query param (browsers can't set WS headers). */
  authToken: string;
  /** The meeting to subscribe to on open. */
  meeting: { platform: Platform | string; native_id: string };
  /** Normalized meeting/bot status (e.g. "active", "needs_human_help"). */
  onStatus?: (status: MeetingStatus | string) => void;
  /** A transcript update (bundle or single segment). */
  onTranscript?: (update: TranscriptUpdate) => void;
  /** A chat message from the bot. */
  onChat?: (frame: ChatMessageFrame) => void;
  /** A protocol/auth error code from the server. */
  onError?: (error: string) => void;
}

export interface WsClient {
  /** Connect, wire handlers, and arm the open → subscribe + ping flow. */
  start(): void;
  /** Close the transport and stop pinging. */
  stop(): void;
}

/**
 * Normalize a raw `meeting.status` value into the dashboard's vocabulary.
 *
 * The only divergence the dashboard owns is `needs_help` (a surface name some forwarders emit) →
 * `needs_human_help` (the api.v1-sealed enum value the dashboard renders), per ADR-0023. Everything
 * else passes through untouched — the WS `payload.status` is an open string, additive by design.
 */
export function normalizeStatus(status: string): MeetingStatus | string {
  return status === "needs_help" ? "needs_human_help" : status;
}

/** `wsUrl` + `?api_key=<token>` (preserving any existing query string). */
function buildWsUrl(wsUrl: string, authToken: string): string {
  const sep = wsUrl.includes("?") ? "&" : "?";
  return `${wsUrl}${sep}api_key=${encodeURIComponent(authToken)}`;
}

export function createWsClient(opts: CreateWsClientOptions): WsClient {
  const { transport, wsUrl, authToken, meeting, onStatus, onTranscript, onChat, onError } = opts;

  let pingTimer: ReturnType<typeof setInterval> | null = null;

  // ── ONE dispatch table — frame.type → handler ──────────────────────────────────────────────────
  const dispatch: Record<string, (frame: WsFrame) => void> = {
    "meeting.status": (frame) => {
      const f = frame as MeetingStatusFrame;
      onStatus?.(normalizeStatus(String(f.payload?.status ?? "")));
    },
    transcript: (frame) => {
      const f = frame as TranscriptFrame;
      onTranscript?.({
        speaker: f.speaker ?? null,
        confirmed: f.confirmed ?? [],
        pending: f.pending ?? [],
      });
    },
    transcription_segment: (frame) => {
      const f = frame as TranscriptionSegmentFrame;
      // Wrap the single segment so consumers see one shape. The frame IS the segment body.
      onTranscript?.({ segments: [f as unknown as TranscriptSegment] });
    },
    chat_message: (frame) => {
      onChat?.(frame as ChatMessageFrame);
    },
    error: (frame) => {
      onError?.((frame as ErrorFrame).error);
    },
    subscribed: () => {
      /* server ack — no-op */
    },
    pong: () => {
      /* keepalive ack — no-op */
    },
  };

  function handleMessage(data: string): void {
    let frame: WsFrame;
    try {
      frame = JSON.parse(data) as WsFrame;
    } catch {
      // Malformed inbound frame — surface it, don't crash the loop.
      onError?.("invalid_json");
      return;
    }
    const handler = dispatch[(frame as { type?: string }).type ?? ""];
    handler?.(frame); // unknown frame types are silently ignored (additive contract)
  }

  function handleOpen(): void {
    transport.send(
      JSON.stringify({
        action: "subscribe",
        meetings: [{ platform: meeting.platform, native_id: meeting.native_id }],
      }),
    );
    pingTimer = setInterval(() => {
      transport.send(JSON.stringify({ action: "ping" }));
    }, PING_INTERVAL_MS);
  }

  function clearPing(): void {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  return {
    start() {
      transport.onMessage(handleMessage);
      transport.onOpen(handleOpen);
      transport.onClose(clearPing);
      transport.connect(buildWsUrl(wsUrl, authToken));
    },
    stop() {
      clearPing();
      transport.close();
    },
  };
}
