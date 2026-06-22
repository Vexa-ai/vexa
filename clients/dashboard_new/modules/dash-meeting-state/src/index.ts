/**
 * @vexa/dash-meeting-state — the meeting FSM + live transcript assembly.
 *
 * A tiny framework-agnostic OBSERVABLE STORE (deliberately NOT Zustand) that composes the dashboard's
 * infra bricks through INJECTED PORTS. It is the single source of truth for one meeting's live view:
 * its status, its merged transcript, and its chat — assembled from REST (seed) + WS (live), and
 * exposed as immutable snapshots a UI layer can subscribe to.
 *
 *   createMeetingState({ apiClient, wsClientFactory, meeting }) → {
 *     getState(), subscribe(cb), bootstrap(), connectLive(), stop()
 *   }
 *
 * Composition (no brick is imported by package name — they're satisfied structurally so this brick
 * stays decoupled and testable over the fakes):
 *   • `apiClient`        — the @vexa/dash-api-client ApiClient port. `bootstrap()` pulls
 *                          getTranscripts → seeds confirmed segments via the two-map model.
 *   • `wsClientFactory`  — builds a @vexa/dash-ws WsClient wired to THIS store's reducers:
 *                          onStatus → set status (close on completed/failed),
 *                          onTranscript → merge the 0.10.6 two-map tick,
 *                          onChat → append.
 *
 * The merge is the exact 0.10.6 TWO-MAP model: confirmed is append-only by segment_id, pending is
 * replaced per speaker per tick (see transcript-merge.ts, grounded on the vendored
 * use-live-transcripts.ts). Status flows through the dashboard vocabulary the WS client already
 * normalized (needs_help → needs_human_help, per ADR-0023).
 */
import type {
  Platform,
  MeetingStatus,
  TranscriptSegment,
  ChatMessageFrame,
  TranscriptionResponse,
} from "@vexa/dash-contracts";
import { createTranscriptManager } from "@vexaai/transcript-rendering";
import { restSegmentToLive, normalizeLiveSeg } from "./transcript-merge.js";

// Re-export the canonical two-map pipeline (`@vexaai/transcript-rendering`) + the dashboard's segment
// adapters as the brick's public surface — the rendering engine is the package, not a local copy.
export {
  createTranscriptState,
  bootstrapConfirmed,
  applyTranscriptTick,
  recomputeTranscripts,
  createTranscriptManager,
} from "@vexaai/transcript-rendering";
export type { TranscriptState } from "@vexaai/transcript-rendering";
export { restSegmentToLive, normalizeLiveSeg } from "./transcript-merge.js";

// ── Injected ports — structural slices of the infra bricks (no package coupling) ───────────────────

/** What `onTranscript` delivers — the @vexa/dash-ws `TranscriptUpdate` shape. */
export interface TranscriptUpdate {
  speaker?: string | null;
  confirmed?: TranscriptSegment[];
  pending?: TranscriptSegment[];
  /** present for a single `transcription_segment` frame (wrapped so consumers see one shape). */
  segments?: TranscriptSegment[];
}

/** The slice of @vexa/dash-api-client's `ApiClient` this brick consumes. */
export interface MeetingStateApiClient {
  getTranscripts(
    platform: Platform | string,
    nativeId: string,
  ): Promise<TranscriptionResponse>;
}

/** The @vexa/dash-ws `WsClient` lifecycle handle. */
export interface MeetingStateWsClient {
  start(): void;
  stop(): void;
}

/** The reducer wiring a `wsClientFactory` must connect a fresh WS client to. */
export interface WsWiring {
  meeting: { platform: Platform | string; native_id: string };
  onStatus: (status: MeetingStatus | string) => void;
  onTranscript: (update: TranscriptUpdate) => void;
  onChat: (frame: ChatMessageFrame) => void;
  onError?: (error: string) => void;
}

/** Builds a started-on-`start()` WS client wired to the given reducers (e.g. wraps `createWsClient`). */
export type WsClientFactory = (wiring: WsWiring) => MeetingStateWsClient;

/** The meeting handle this store tracks. */
export interface MeetingHandle {
  platform: Platform | string;
  native_id: string;
  id?: number | string;
}

export interface CreateMeetingStateOptions {
  apiClient: MeetingStateApiClient;
  wsClientFactory: WsClientFactory;
  meeting: MeetingHandle;
  /**
   * The meeting's current status from REST (the caller's `getMeeting`), used as the store's initial
   * status so a TERMINAL or reopened meeting renders its real status immediately — before (or without)
   * any live WS frame. Defaults to "requested" (a fresh spawn). Live `meeting.status` frames override it.
   */
  initialStatus?: MeetingStatus | string;
}

/** The connection sub-state of the store. */
export type ConnectionState = "idle" | "connecting" | "live" | "closed";

/** The immutable snapshot subscribers receive. */
export interface MeetingState {
  status: MeetingStatus | string;
  segments: TranscriptSegment[];
  chat: ChatMessageFrame[];
  connection: ConnectionState;
}

export interface MeetingStateStore {
  /** Current immutable snapshot. */
  getState(): MeetingState;
  /** Subscribe to snapshots; returns an unsubscribe fn. The cb fires on every state change. */
  subscribe(cb: (state: MeetingState) => void): () => void;
  /** Seed segments from REST (getTranscripts). Idempotent — safe to call once before connectLive. */
  bootstrap(): Promise<void>;
  /** Build + start the WS client, wiring live status / transcript / chat into this store. */
  connectLive(): void;
  /** Stop the WS client and mark the connection closed. */
  stop(): void;
}

/** A terminal status closes the live socket (the meeting is over). */
function isTerminal(status: MeetingStatus | string): boolean {
  return status === "completed" || status === "failed";
}

export function createMeetingState(opts: CreateMeetingStateOptions): MeetingStateStore {
  const { apiClient, wsClientFactory, meeting, initialStatus } = opts;

  // The canonical `@vexaai/transcript-rendering` manager owns the two-map model + dedup + sort;
  // `state.segments` is its projection. Segments are adapted to the renderer shape before they enter it.
  const manager = createTranscriptManager();

  let state: MeetingState = {
    status: initialStatus ?? "requested",
    segments: [],
    chat: [],
    connection: "idle",
  };

  const subscribers = new Set<(state: MeetingState) => void>();
  let wsClient: MeetingStateWsClient | null = null;

  function emit(): void {
    for (const cb of subscribers) cb(state);
  }

  /** Replace state with a shallow merge and notify subscribers. */
  function setState(patch: Partial<MeetingState>): void {
    state = { ...state, ...patch };
    emit();
  }

  // ── reducers wired into the WS client ────────────────────────────────────────────────────────────

  function onStatus(status: MeetingStatus | string): void {
    setState({ status });
    if (isTerminal(status)) {
      // Meeting ended — tear the socket down and mark the connection closed.
      stop();
    }
  }

  function onTranscript(update: TranscriptUpdate): void {
    // A `transcription_segment` frame arrives as { segments:[seg] } — treat each as a confirmed seg.
    // Every segment is adapted to the renderer shape (epoch start → absolute_start_time) before it
    // enters the manager, since the live WS frame omits the `absolute_start_time` the renderer requires.
    const confirmed = (update.confirmed ?? update.segments ?? []).map(normalizeLiveSeg);
    const pending = (update.pending ?? []).map(normalizeLiveSeg);
    const speaker = update.speaker ?? undefined;
    const merged = manager.handleMessage({ type: "transcript", speaker, confirmed, pending });
    if (merged) setState({ segments: merged as unknown as TranscriptSegment[] });
  }

  function onChat(frame: ChatMessageFrame): void {
    setState({ chat: [...state.chat, frame] });
  }

  return {
    getState() {
      return state;
    },

    subscribe(cb) {
      subscribers.add(cb);
      return () => {
        subscribers.delete(cb);
      };
    },

    async bootstrap() {
      const resp = await apiClient.getTranscripts(meeting.platform, meeting.native_id);
      const live = (resp.segments ?? []).map(restSegmentToLive);
      const seeded = manager.bootstrap(live);
      // REST also carries the authoritative current status — seed it.
      setState({ segments: seeded as unknown as TranscriptSegment[], status: resp.status ?? state.status });
    },

    connectLive() {
      // Already connecting/live → no double-connect. Already closed (terminal status, or an explicit
      // stop()) → stay closed; the meeting is over, there's nothing to reconnect to.
      if (state.connection !== "idle") return;
      setState({ connection: "connecting" });
      wsClient = wsClientFactory({
        meeting: { platform: meeting.platform, native_id: meeting.native_id },
        onStatus,
        onTranscript,
        onChat,
      });
      wsClient.start();
      setState({ connection: "live" });
    },

    stop,
  };

  function stop(): void {
    if (wsClient) {
      wsClient.stop();
      wsClient = null;
    }
    if (state.connection !== "closed") setState({ connection: "closed" });
  }
}
