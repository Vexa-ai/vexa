/** chatStream — the resumable SSE reader for a chat turn (POST /api/chat).
 *
 *  Why this exists as its own unit: a chat turn spawns a FRESH per-dispatch agent worker (docker
 *  backend) that takes several seconds to boot before its first token. If the SSE closes during that
 *  cold-start window — or drops mid-turn on a transient proxy/network blip — the turn is NOT lost: the
 *  worker completes + commits, and its output Stream is DURABLE and id-addressable (agent-api surfaces
 *  each event's Stream cursor as the SSE `id:`). So instead of declaring "No chat output arrived before
 *  the stream closed" the moment a stream ends early, we RESUME from the last-seen cursor (Last-Event-ID)
 *  and keep rendering — gapless, mirroring /api/meeting/stream. Only a genuinely stuck turn (no output
 *  and no clean end well past a normal cold start) or a real upstream error surfaces a failure.
 *
 *  Extracted from Chat.send so the robustness logic is unit-testable against a faked fetch/SSE. */

import { noteAuthFailure, isAuthStatus } from "@/app/session";
import { SESSION_ENDED_HEADLINE } from "./apiClient";

/** A parsed SSE event off the chat stream. `type` is the discriminator; other fields are per-type. */
export type ChatStreamEvent = {
  type: string;
  text?: string;
  tool?: string;
  sha?: string;
  ok?: boolean;
  reply?: string;
  message?: string;
  /** `error` events only — the upstream HTTP status the proxy folded into the stream. /api/chat
   *  answers 200 with an `error` event even when the gateway refused with a 401, so this field is
   *  the ONLY place the auth status survives into the client. */
  status?: number;
  /** `artifact` events only — a file a tool WROTE, and whether it should come to the front.
   *  `workspace` is "" when the write landed on the caller's own desk: the server's record resolves
   *  that, and the stream deliberately does not guess, so an empty string means "no slug" rather
   *  than "unknown". */
  workspace?: string;
  path?: string;
  focus?: boolean;
};

/** HOW ONE INTERIM TEXT JOINS THE LAST (F40, founder ruling 2026-09-02).
 *
 *  `message-delta` carries two different things depending on how the worker's model backend is
 *  running: with partial streaming it is a TOKEN, and without it, a whole assistant text block.
 *  Plain concatenation is right for the first and wrong for the second, and the founder read the
 *  result on screen: `"created here.I'll set up a shared workspace…"` — two separate narrations run
 *  together into a sentence that reads as one and parses as neither.
 *
 *  The boundary that is actually observable from the stream is a TOOL CALL: an assistant message
 *  ends when the model reaches for a tool, and text arriving after the tool result is a NEW message.
 *  So `afterTool` is armed by a tool-call and spent by the next delta. Tokens inside one block never
 *  have a tool call between them, so nothing is ever broken mid-sentence.
 *
 *  Paragraphs rather than folding the narration under the step line: those interim texts ARE the
 *  agent saying what it is about to do, and a person watching a turn in flight is reading exactly
 *  them. Collapsing them by default would hide the only running commentary there is — to fix a
 *  problem that is a missing blank line.
 *
 *  Pure, and exported, because the rule is one line of string handling that is impossible to see
 *  wrong by reading it and trivial to see wrong in a test. */
export function joinInterim(prev: string, next: string, afterTool: boolean): string {
  if (!afterTool || !prev.trim() || prev.endsWith("\n")) return prev + next;
  return prev + "\n\n" + next;
}

/** The live phase of a turn, surfaced so the pane is VERBOSE about what's happening instead of going
 *  silently stale: `connecting` (cold-starting the worker, no output yet) · `working` (output seen, but
 *  quiet right now — the agent is thinking / a tool is running) · `reconnecting` (the stream dropped, we
 *  are resuming from the cursor) · `stalled` (the stream is open but sending nothing — we're forcing a
 *  reconnect). `null` clears the indicator (real output is flowing or the turn ended). */
export type ChatPhase = "connecting" | "working" | "reconnecting" | "stalled";

export type ChatStreamCallbacks = {
  /** an agent message-delta with non-empty text (the first one clears the "starting" placeholder) */
  onDelta: (text: string) => void;
  /** a tool-call step to show as an operation */
  onTool: (tool: string, args?: Record<string, unknown>) => void;
  /** the turn committed to the workspace (terminal) */
  onCommit: (sha: string | undefined) => void;
  /** the turn was rejected by workspace.v1 governance (terminal) */
  onRejected: () => void;
  /** a done event with ok=false — model inference failed (terminal, surfaced) */
  onModelFailure: (reply: string | undefined) => void;
  /** a hard upstream error the proxy folded into the stream (terminal, surfaced) */
  onError: (message: string) => void;
  /** we are (re)connecting and no output has shown yet — show/keep a "starting agent…" affordance */
  onStarting: () => void;
  /** the live phase changed (or a heartbeat fired while quiet) — drive a verbose status line. `null`
   *  clears it. Optional so existing callers/tests need not implement it. */
  onStatus?: (phase: ChatPhase | null) => void;
  /** a chunk was consumed — a hook for autoscroll */
  onProgress?: () => void;
  /** A FILE THE TURN JUST WROTE (F41). Emitted after the matching `tool-result` and only on
   *  success — a failed write says nothing. Optional so existing callers/tests need not implement
   *  it. */
  onArtifact?: (a: { workspace: string; path: string; focus: boolean }) => void;
};

export type ChatStreamRequest = {
  /** the built prompt for this turn */
  prompt: string;
  /** the chat session/thread id (the warm unit keys on subject+session) */
  session: string;
  /** the active center-tab grounding (legacy mirror), or undefined */
  active: unknown;
  /** the terminal-state context bundle {tz, surface, focus, include}, or undefined */
  context?: unknown;
  /** THE SCAFFOLD this turn opened from (PRD §5.5), on the FIRST turn only.
   *
   *  One record, two renderers: the panel rendered its tabs from this id and dispatch reads its
   *  mounts and opening from the same one. Absent on every later turn — the session carries the
   *  thread from then on, and re-sending it would invite the server to re-apply an arrival that
   *  already happened. */
  scaffold_id?: string;
};

/** One nonce per user turn, constant across that turn's reconnect attempts. It lets agent-api tell a
 *  NO-CURSOR retry (the stream dropped before any `id:` arrived, so Last-Event-ID is empty) from a new
 *  turn with identical text — a matching nonce re-attaches to the same turn instead of dispatching a
 *  second one. */
function mintTurnId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export type ChatStreamOptions = {
  /** injected for tests; defaults to global fetch */
  fetchImpl?: typeof fetch;
  /** cancels the in-flight fetch + the resume loop */
  signal: AbortSignal;
  /** hard cap well beyond a cold start — a turn quiet this long is genuinely stuck */
  hardTimeoutMs?: number;
  /** delay between resume attempts after an early close */
  reconnectBackoffMs?: number;
  /** while a read is outstanding, emit a `working`/`connecting` heartbeat every this-many ms so the pane
   *  never looks frozen during a long think / tool run */
  heartbeatMs?: number;
  /** no bytes for this long on an OPEN stream ⇒ treat it as STALLED and reconnect from the cursor (the
   *  fix for a silently-broken SSE that would otherwise freeze until the hard cap) */
  idleReconnectMs?: number;
  /** injected for tests so a fake clock/no real wait is possible */
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

export type ChatStreamResult = {
  /** any renderable output arrived (delta text / tool / a surfaced failure) */
  sawVisibleOutput: boolean;
  /** the turn reached a genuine end (turn-complete / commit / done / rejected / error) */
  terminal: boolean;
  /** aborted by the caller (stop button / unmount) */
  aborted: boolean;
};

const DEFAULT_HARD_TIMEOUT_MS = 90000;   // >> a normal cold start
const DEFAULT_RECONNECT_BACKOFF_MS = 800;
const DEFAULT_HEARTBEAT_MS = 3500;       // "still working…" cadence during a quiet read
const DEFAULT_IDLE_RECONNECT_MS = 18000; // an OPEN-but-silent stream this long ⇒ stalled → reconnect

/** Split accumulated SSE text into complete lines, returning [lines, remainder]. */
function takeLines(buf: string): [string[], string] {
  const lines = buf.split("\n");
  const remainder = lines.pop() ?? "";
  return [lines, remainder];
}

/**
 * Stream a chat turn, resuming across early closes. Resolves when the turn genuinely ends, the caller
 * aborts, or the hard timeout elapses with no output. Throws only on a fetch/reader error that is NOT an
 * abort (the caller maps that to a visible message).
 */
export async function streamChatTurn(
  req: ChatStreamRequest,
  cb: ChatStreamCallbacks,
  opts: ChatStreamOptions,
): Promise<ChatStreamResult> {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const now = opts.now ?? (() => Date.now());
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const hardTimeoutMs = opts.hardTimeoutMs ?? DEFAULT_HARD_TIMEOUT_MS;
  const backoffMs = opts.reconnectBackoffMs ?? DEFAULT_RECONNECT_BACKOFF_MS;
  const heartbeatMs = opts.heartbeatMs ?? DEFAULT_HEARTBEAT_MS;
  const idleReconnectMs = opts.idleReconnectMs ?? DEFAULT_IDLE_RECONNECT_MS;
  const signal = opts.signal;

  let sawVisibleOutput = false;
  let terminal = false;
  let lastEventId: string | null = null;
  const turnId = mintTurnId();
  const startedAt = now();

  cb.onStarting();
  cb.onStatus?.("connecting");

  /** Read the next chunk, but never block forever: emit a `working`/`connecting` heartbeat every
   *  `heartbeatMs` while waiting, and if an OPEN stream sends NOTHING for `idleReconnectMs`, return
   *  `"stalled"` so the caller reconnects from the cursor (recovers a silently-broken SSE). Uses real
   *  timers — in tests the faked reads resolve immediately, so the timers are set-then-cleared and never
   *  fire. */
  const readOrStall = async (
    reader: ReadableStreamDefaultReader<Uint8Array>,
  ): Promise<{ done: boolean; value?: Uint8Array } | "stalled"> => {
    let hb: ReturnType<typeof setInterval> | undefined;
    let to: ReturnType<typeof setTimeout> | undefined;
    try {
      const guard = new Promise<"stalled">((resolve) => {
        hb = setInterval(() => cb.onStatus?.(sawVisibleOutput ? "working" : "connecting"), heartbeatMs);
        to = setTimeout(() => resolve("stalled"), idleReconnectMs);
      });
      return await Promise.race([reader.read(), guard]);
    } finally {
      if (hb !== undefined) clearInterval(hb);
      if (to !== undefined) clearTimeout(to);
    }
  };

  const drainOnce = async (): Promise<"closed" | "terminal"> => {
    // On a RECONNECT, Last-Event-ID makes agent-api re-attach to the SAME warm unit and resume from the
    // cursor — NO second turn is dispatched. The first attempt sends no cursor (fresh dispatch).
    let r: Response;
    try {
      r = await fetchImpl("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}) },
        // `scaffold_id` is OMITTED when absent rather than sent as null: agent-api's ChatBody is
        // `extra="forbid"`-adjacent about shapes, and an explicit null is a different statement
        // from "this turn did not come from an arrival".
        body: JSON.stringify({ prompt: req.prompt, session: req.session, active: req.active, context: req.context, turn_id: turnId,
          ...(req.scaffold_id ? { scaffold_id: req.scaffold_id } : {}) }),
        signal,
      });
    } catch (e) {
      if (signal.aborted) throw e;      // user stop → propagate
      return "closed";                  // the POST itself failed (a network blip) → resume from the cursor
    }
    if (!r.ok) {
      // 4xx is a real client/terminal error — surface it, don't retry for the whole hard cap. A 5xx
      // (transient gateway/upstream) is resumable → reconnect from the cursor.
      if (r.status < 500) {
        terminal = true;
        // An auth-shaped refusal is the SESSION, not this turn: tell the watcher and say so plainly
        // instead of leaving a status code in the transcript.
        noteAuthFailure(r.status, "/api/chat");
        cb.onError(isAuthStatus(r.status) ? SESSION_ENDED_HEADLINE : `Chat request failed (${r.status})`);
        return "terminal";
      }
      return "closed";
    }
    const reader = r.body?.getReader();
    if (!reader) return "closed";
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      let res: { done: boolean; value?: Uint8Array } | "stalled";
      try {
        res = await readOrStall(reader);
      } catch (e) {
        if (signal.aborted) throw e;    // user stop → propagate
        // A mid-stream read/network error (reader.read() rejected) is NOT the end of the turn — the worker
        // is still running server-side and its output Stream is durable. Reconnect from the cursor instead
        // of surfacing a raw "network error"; the outer loop's hard cap bounds the retries.
        cb.onStatus?.("reconnecting");
        try { await reader.cancel("stream-error"); } catch { /* ignore */ }
        return terminal ? "terminal" : "closed";
      }
      if (res === "stalled") {
        // Open stream, no bytes for idleReconnectMs → force a reconnect from the cursor rather than hang.
        cb.onStatus?.("stalled");
        try { await reader.cancel("idle"); } catch { /* ignore */ }
        return terminal ? "terminal" : "closed";
      }
      const { value: chunk, done } = res;
      if (done) return terminal ? "terminal" : "closed";
      buf += dec.decode(chunk, { stream: true });
      const [lines, remainder] = takeLines(buf);
      buf = remainder;
      for (const line of lines) {
        if (line.startsWith("id: ")) { lastEventId = line.slice(4).trim() || lastEventId; continue; }
        if (!line.startsWith("data: ")) continue;
        let ev: ChatStreamEvent;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        switch (ev.type) {
          // The worker's liveness ack — emitted the moment a turn is picked up (warm or cold), long
          // before the first model token. Flips the heartbeat "Starting agent" → "Working"
          // honestly: the agent IS working, it just hasn't produced output yet.
          case "turn-accepted":
            sawVisibleOutput = true;
            cb.onStatus?.("working");
            break;
          case "message-delta":
            if (ev.text) { sawVisibleOutput = true; cb.onDelta(ev.text); }
            break;
          case "tool-call":
            sawVisibleOutput = true; cb.onTool(ev.tool ?? "tool", (ev as { args?: Record<string, unknown> }).args);
            break;
          // A SUCCESSFUL WRITE TO A MOUNTED WORKSPACE (F41). The founder created a shared workspace,
          // the agent wrote its README, and the right panel stayed on `_global/README.md` — the one
          // document the turn had just made was the one thing not on screen. The event is
          // advisory-only here: this reader forwards it and the shell decides, because the tab set
          // belongs to the CHAT RECORD (decision 18) and this file owns no state at all.
          case "artifact":
            if (ev.path) cb.onArtifact?.({ workspace: ev.workspace ?? "", path: ev.path, focus: ev.focus === true });
            break;
          case "commit":
            terminal = true; cb.onCommit(ev.sha);
            break;
          case "rejected":
            terminal = true; cb.onRejected();
            break;
          case "turn-complete":
            terminal = true;
            break;
          case "done":
            terminal = true;
            if (ev.ok === false) { sawVisibleOutput = true; cb.onModelFailure(ev.reply); }
            break;
          // A hard upstream error the proxy folded into the stream: the turn genuinely failed — surface
          // it (do NOT treat as a cold-start close to resume).
          //
          // ⚠ /api/chat answers 200 and folds the gateway's refusal into THIS event, so a revoked
          // session arrives here — carrying `status: 401` and, as `message`, the gateway's raw JSON
          // body. That body is payload-shaped, so the presenter could not read it as prose and the
          // turn died under "Something went wrong — details are in the browser console." That was
          // the founder's whole symptom. An auth status is now read off the event, reported to the
          // session watcher, and rendered as the session sentence.
          case "error":
          case "stream-error": {
            terminal = true;
            sawVisibleOutput = true;
            const authFailed = typeof ev.status === "number" && isAuthStatus(ev.status);
            if (authFailed) noteAuthFailure(ev.status as number, "/api/chat");
            cb.onError(authFailed ? SESSION_ENDED_HEADLINE : (ev.message || "Chat request failed."));
            break;
          }
          default:
            break;
        }
      }
      cb.onProgress?.();
    }
  };

  while (!signal.aborted) {
    const outcome = await drainOnce();
    if (outcome === "terminal" || terminal) break;
    if (now() - startedAt > hardTimeoutMs) break;
    cb.onStarting();  // still waiting on the worker — keep the pane honest between attempts
    cb.onStatus?.("reconnecting");
    await sleep(backoffMs);
  }

  cb.onStatus?.(null);  // turn ended (or gave up) — drop the live indicator
  return { sawVisibleOutput, terminal, aborted: signal.aborted };
}
