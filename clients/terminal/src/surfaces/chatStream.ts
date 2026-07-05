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

/** A parsed SSE event off the chat stream. `type` is the discriminator; other fields are per-type. */
export type ChatStreamEvent = {
  type: string;
  text?: string;
  tool?: string;
  sha?: string;
  ok?: boolean;
  reply?: string;
  message?: string;
};

export type ChatStreamCallbacks = {
  /** an agent message-delta with non-empty text (the first one clears the "starting" placeholder) */
  onDelta: (text: string) => void;
  /** a tool-call step to show as an operation */
  onTool: (tool: string) => void;
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
  /** a chunk was consumed — a hook for autoscroll */
  onProgress?: () => void;
};

export type ChatStreamRequest = {
  /** the built prompt for this turn */
  prompt: string;
  /** the chat session/thread id (the warm unit keys on subject+session) */
  session: string;
  /** the active center-tab grounding, or undefined */
  active: unknown;
};

export type ChatStreamOptions = {
  /** injected for tests; defaults to global fetch */
  fetchImpl?: typeof fetch;
  /** cancels the in-flight fetch + the resume loop */
  signal: AbortSignal;
  /** hard cap well beyond a cold start — a turn quiet this long is genuinely stuck */
  hardTimeoutMs?: number;
  /** delay between resume attempts after an early close */
  reconnectBackoffMs?: number;
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
  const signal = opts.signal;

  let sawVisibleOutput = false;
  let terminal = false;
  let lastEventId: string | null = null;
  const startedAt = now();

  cb.onStarting();

  const drainOnce = async (): Promise<"closed" | "terminal"> => {
    // On a RECONNECT, Last-Event-ID makes agent-api re-attach to the SAME warm unit and resume from the
    // cursor — NO second turn is dispatched. The first attempt sends no cursor (fresh dispatch).
    const r = await fetchImpl("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}) },
      body: JSON.stringify({ prompt: req.prompt, session: req.session, active: req.active }),
      signal,
    });
    if (!r.ok) throw new Error(`Chat request failed (${r.status})`);
    const reader = r.body?.getReader();
    if (!reader) return "closed";
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value: chunk, done } = await reader.read();
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
          case "message-delta":
            if (ev.text) { sawVisibleOutput = true; cb.onDelta(ev.text); }
            break;
          case "tool-call":
            sawVisibleOutput = true; cb.onTool(ev.tool ?? "tool");
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
          case "error":
          case "stream-error":
            terminal = true; sawVisibleOutput = true; cb.onError(ev.message || "Chat request failed.");
            break;
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
    await sleep(backoffMs);
  }

  return { sawVisibleOutput, terminal, aborted: signal.aborted };
}
