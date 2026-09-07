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

import type { ChatIntent } from "./chatIntent";
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
  /** `done` events only — WHAT THE TURN GAVE UP (F89): its tool-call or wall-clock budget ran out,
   *  or its context was trimmed to fit. Absent on a turn that finished under its own budgets. */
  reason?: string;
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
  /** `artifact` events only — KEEP this page in the chat's strip, as a pinned entry, exactly as a
   *  scaffold-declared pinned tab does. Orthogonal to `focus`: pinning is about what stays,
   *  focusing is about what is in front, and a turn may ask for either, both, or neither. */
  pin?: boolean;
  /** `focus` events only (Vexa-ai/vexa#1603) — the workspace a `workspace_new` just made, and its
   *  human name when the create knew one. Never a path: this event names a place, not a page. */
  name?: string;
  /** `terms` events only (PRD decision 35) — the meeting ROW the chips belong to, the cursor the
   *  next Highlight should send back, and the published terms themselves. */
  meeting?: string;
  cursor?: string;
  terms?: unknown[];
  // A BACKGROUND JOB (Vexa-ai/vexa#1584). `job_id` is on every event a job produced, and on the
  // `job-started` the spawning turn emits; `kind`/`target`/`line` are the job's own three facts.
  //
  // `target` is shared with the `open` event below, where it is the TOKEN the agent named
  // (`meeting:transcript`, `meeting:note`, a path). Carried for the record only: an `open` also
  // carries `workspace`/`path`, already resolved by the tool, and those are the two fields the
  // panel reads. The client never re-derives what the server has answered.
  job_id?: string;
  kind?: string;
  target?: string;
  line?: string;
  /** WHICH CONVERSATION THE JOB BELONGS TO (Vexa-ai/vexa#1613). Stamped by the worker on every job
   *  event. A connection reading a chat it does not name renders none of it — see `ownsJob`. */
  session?: string;
  /** `job-progress` only — the window that just ended and the job's running step count. */
  window?: number;
  calls?: number;
  /** `job-queued` only (Vexa-ai/vexa#1610) — how many acts are in front of this one on its page. */
  ahead?: number;
  /** HOW MANY STEPS THE TURN TOOK, from the server (Vexa-ai/vexa#1622) — on `done` (the harness's
   *  own count, whole turn) and on `turn-complete` (the worker's). Until this existed the only step
   *  count anywhere was one each browser derived by counting the `tool-call` events it happened to
   *  see, which is short by however much of the turn it missed. `budget` is the ceiling that count
   *  was measured against. */
  steps?: number;
  budget?: number;
  /** `done` only, and only when the turn STOPPED at a budget (Vexa-ai/vexa#1622) — the act the
   *  bubble offers, and the words it puts back on the same target. */
  act?: { label?: string; instruction?: string };
};

/** IS THIS JOB THIS CHAT'S? (Vexa-ai/vexa#1613.)
 *
 *  The founder opened a NEW EMPTY CHAT and its first content was two lines from another chat's
 *  jobs — *"some leak to empty chat"*. The server half of that is fixed where it was caused (the
 *  jobs register is shared by every chat a person has, and a booting worker was reporting other
 *  conversations' LIVE jobs as its own restart casualties). This is the other half, and it is the
 *  one that makes the promise unconditional: a job event names its session, and a connection
 *  belonging to a different one drops it whole.
 *
 *  It fails OPEN on an unstamped event, deliberately: a deployment one release behind stamps
 *  nothing, and refusing every job line there would replace a rare wrong line with a permanent
 *  missing one. The stamp is what removes the doubt, not the client's suspicion. */
export function ownsJob(ev: { session?: string }, session: string | undefined): boolean {
  const owner = (ev.session ?? "").trim();
  return !owner || !session || owner === session;
}

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
  /** THE TURN GAVE SOMETHING UP (F89): `done.reason` — it hit its tool-call or wall-clock budget,
   *  or answered from a context it had trimmed. The harness emitted `turn-truncated` /
   *  `context-trimmed` for this and NOTHING consumed them, so a half-finished turn was rendered as
   *  a finished one. `partial` is whatever reply the turn did produce; it is still worth showing.
   *  Optional so existing callers/tests need not implement it — when it is absent an `ok=false`
   *  done still falls through to `onModelFailure`.
   *
   *  `stop` (Vexa-ai/vexa#1622) carries what the harness knows and prose cannot: the step count,
   *  the budget it ran into, and the CONTINUE ACT — so the bubble can offer one press instead of
   *  leaving the person to re-type the instruction, which is what the founder did three times in
   *  one conversation before this shipped. Optional field on an optional callback: a deployment one
   *  release behind sends no `act` and the reason line renders alone, exactly as it did. */
  onTruncated?: (reason: string, partial: string | undefined,
                 stop?: { steps?: number; budget?: number;
                          act?: { label: string; instruction: string } }) => void;
  /** THE SERVER'S STEP COUNT for this turn (Vexa-ai/vexa#1622) — off `done` or `turn-complete`,
   *  whichever arrives. Optional so existing callers/tests need not implement it. */
  onSteps?: (steps: number) => void;
  /** a hard upstream error the proxy folded into the stream (terminal, surfaced) */
  onError: (message: string) => void;
  /** we are (re)connecting and no output has shown yet — show/keep a "starting agent…" affordance */
  onStarting: () => void;
  /** THE WORKER HAS TAKEN A TURN — its liveness ack (Vexa-ai/vexa#1610 reads it for a second
   *  reason: a turn being taken is exactly the moment something LEAVES the session's inbox, so this
   *  is when the queued rows are worth re-reading). Optional; existing callers need not implement it. */
  onAccepted?: () => void;
  /** the live phase changed (or a heartbeat fired while quiet) — drive a verbose status line. `null`
   *  clears it. Optional so existing callers/tests need not implement it. */
  onStatus?: (phase: ChatPhase | null) => void;
  /** a chunk was consumed — a hook for autoscroll */
  onProgress?: () => void;
  /** A FILE THE TURN JUST WROTE (F41). Emitted after the matching `tool-result` and only on
   *  success — a failed write says nothing. Optional so existing callers/tests need not implement
   *  it. */
  onArtifact?: (a: { workspace: string; path: string; focus: boolean; pin: boolean }) => void;
  /** SOMEBODY ASKED TO SEE THIS (Vexa-ai/vexa#1586) — a successful `open_page`. Different from
   *  `onArtifact` in the one way that matters: an artifact is the turn saying "I wrote this" and
   *  must not interrupt a reader who has opened something else, while this IS what the reader
   *  asked for, so it always comes to the front. There is no `focus` flag for the same reason. */
  onOpen?: (o: { workspace: string; path: string; target: string }) => void;
  /** A WORKSPACE THE TURN CREATED (Vexa-ai/vexa#1603) — it is part of this chat from now on.
   *  Forwarded, never stored, exactly like `onArtifact`: the focus set belongs to the CHAT RECORD
   *  and this reader owns no state. Optional so existing callers/tests need not implement it. */
  onFocusWorkspace?: (f: { workspace: string; name: string }) => void;
  /** TERMS THE TURN PUBLISHED for a meeting's transcript (decision 35.2). Forwarded, never stored:
   *  the chips belong to the CHAT RECORD and this reader owns no state, exactly as with
   *  `onArtifact`. Optional so existing callers/tests need not implement it. */
  onTerms?: (t: { meeting: string; cursor: string; terms: unknown[] }) => void;
  /** THIS TURN HANDED ITS WORK TO A BACKGROUND JOB and is already over (Vexa-ai/vexa#1584) — the
   *  chat is free NOW, not when this connection closes. Everything carrying this job id from here
   *  on belongs to the job, not to the turn, so none of it touches the turn's own steps. */
  onJobStarted?: (j: { jobId: string; kind: string; target: string; line: string }) => void;
  /** THE ACT IS QUEUED BEHIND ANOTHER ON THE SAME PAGE (Vexa-ai/vexa#1610). It used to be REFUSED
   *  here — the founder read *"There is already something running on … — I'll finish that one
   *  first"* twice, with his instruction lines gone — and it now waits its turn instead, carrying
   *  the id it will run under. For this connection it is a job from this moment: the turn that asked
   *  is over (so the composer is free), and the view stays open until it has started, run and
   *  landed. */
  onJobQueued?: (j: { jobId: string; kind: string; target: string; line: string; ahead: number }) => void;
  /** one tool call the JOB made — the job's own step count, kept apart from the turn's */
  onJobStep?: (jobId: string, tool: string) => void;
  /** THE JOB IS STILL GOING, in a fresh window (Vexa-ai/vexa#1613). A long act reaches its
   *  per-window tool-call budget, checkpoints — its pages are already committed — and carries on.
   *  The row says so rather than going quiet: a job that looks stopped and is not is the same lie
   *  as a spinner that outlives its turn. */
  onJobProgress?: (jobId: string, line: string) => void;
  /** the job landed, or died. `line` is the one sentence it posts into the chat — never silence. */
  onJobEnd?: (j: { jobId: string; ok: boolean; line: string }) => void;
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
  /** PRD decision 32 — what a button pressed on a page asks for. Typed, never prose: the server
   *  half turns it into the matching preset. Omitted when absent, like `scaffold_id`. */
  intent?: ChatIntent;
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
  /** WATCH, DON'T SEND (Vexa-ai/vexa#1610). The cursor to attach from — the last event this chat
   *  saw on its own output Stream, or the tail the pending route reported on a cold load. It seeds
   *  `Last-Event-ID` on the FIRST attempt, and agent-api never dispatches a turn on a request that
   *  carries one: the submission is already on the server's inbox, so re-sending it is the one thing
   *  that must not happen. Exact by construction — the next events after that cursor ARE the queued
   *  turn's, so nothing replays and nothing is missed. */
  attachFrom?: string;
};

export type ChatStreamResult = {
  /** any renderable output arrived (delta text / tool / a surfaced failure) */
  sawVisibleOutput: boolean;
  /** the turn reached a genuine end (turn-complete / commit / done / rejected / error) */
  terminal: boolean;
  /** aborted by the caller (stop button / unmount) */
  aborted: boolean;
  /** WHERE THIS CHAT HAS READ TO on its output Stream (Vexa-ai/vexa#1610) — the last SSE `id:`. The
   *  next thing the person submitted starts after exactly this point, so it is what the follow-on
   *  attach resumes from: no gap, and no replay of the turn that just finished. */
  cursor: string | null;
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
  let lastEventId: string | null = opts.attachFrom ?? null;
  const turnId = mintTurnId();
  const startedAt = now();
  // THE JOBS THIS CONNECTION STARTED (Vexa-ai/vexa#1584). A SET, not one id: a marked act spawns
  // exactly one, but a turn may call `spawn_job` twice, and a second job nobody was watching would
  // land its page with no line saying where it came from. It changes three things, and only for the
  // connection that owns them: `turn-complete` no longer ends the turn while one is open (the job
  // outlives its turn — `RedisStreamReader` makes the same exception with the same two lines), the
  // hard cap does not apply (a job is allowed to take minutes; that is the point), and every event
  // carrying a job id this connection did not start is skipped, because another turn's connection
  // reads the same Stream and must never fold a foreign job's steps into its own.
  const myJobs = new Set<string>();
  let turnDone = false;

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
    // cursor — NO second turn is dispatched. The first attempt sends no cursor (fresh dispatch)…
    // …UNLESS THIS IS AN ATTACH (Vexa-ai/vexa#1610), where the whole point is that no turn is
    // dispatched: the submission is already on the server's inbox and this call is here to WATCH it
    // run. `opts.attachFrom` seeds the cursor, so the very first request carries the header and the
    // server takes its resume branch.
    let r: Response;
    try {
      r = await fetchImpl("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}) },
        // `scaffold_id` is OMITTED when absent rather than sent as null: agent-api's ChatBody is
        // `extra="forbid"`-adjacent about shapes, and an explicit null is a different statement
        // from "this turn did not come from an arrival".
        body: JSON.stringify({ prompt: req.prompt, session: req.session, active: req.active, context: req.context, turn_id: turnId,
          ...(req.scaffold_id ? { scaffold_id: req.scaffold_id } : {}),
          ...(req.intent ? { intent: req.intent } : {}) }),
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
        // ── the job lane, ahead of the turn's switch ──────────────────────────────────────────
        // A job's events never reach the turn's handlers. They would otherwise land on an agent
        // bubble the person finished reading a minute ago: its step count would climb by itself and
        // its `commit` would end this read while the job was still writing.
        const jid = typeof ev.job_id === "string" ? ev.job_id : "";
        if (ev.type === "job-started") {
          // …AND ONLY A JOB THIS CHAT OWNS IS EVER ADOPTED (Vexa-ai/vexa#1613). Adoption is the
          // one decision that matters: every later event is gated on `myJobs`, so a job never
          // taken here can never render, step a row, or post a line in this conversation.
          //
          // A job ALREADY in the set was QUEUED here a moment ago (Vexa-ai/vexa#1610) and is now
          // starting: it was adopted then, by the same rule, so the callback fires again and the
          // row it owns learns it is running rather than a second row appearing beside it.
          // Idempotent on both sides — `startJob` ignores an id it already has, and `promoteJob`
          // only clears the queued flag.
          if (jid && (myJobs.has(jid) || ownsJob(ev, req.session))) {
            myJobs.add(jid);
            sawVisibleOutput = true;
            cb.onJobStarted?.({ jobId: jid, kind: ev.kind ?? "", target: ev.target ?? "", line: ev.line ?? "" });
          }
          continue;
        }
        // QUEUED, NOT REFUSED. The act is this connection's from here: it owns the row and holds its
        // view open, exactly as it does for one that started immediately — the whole point being
        // that the person still gets the start, the steps and the landing line.
        //
        // `job-collapsed` deliberately has NO branch: nothing new is running, so nothing new is
        // watched, and the line saying where the press went already arrives as this turn's own
        // `message-delta` (the worker composes it once, in `run_message`).
        if (ev.type === "job-queued") {
          // …ADOPTED BY THE SAME RULE AS `job-started` (Vexa-ai/vexa#1613): a queued act is a job
          // this connection is going to render, so a job belonging to another chat must not be
          // taken here either.
          if (jid && !myJobs.has(jid) && ownsJob(ev, req.session)) {
            myJobs.add(jid);
            sawVisibleOutput = true;
            cb.onJobQueued?.({ jobId: jid, kind: ev.kind ?? "", target: ev.target ?? "",
                               line: ev.line ?? "", ahead: typeof ev.ahead === "number" ? ev.ahead : 1 });
          }
          continue;
        }
        if (jid) {
          if (!myJobs.has(jid)) continue;           // somebody else's job on the shared Stream
          if (ev.type === "job-done" || ev.type === "job-failed") {
            myJobs.delete(jid);
            if (turnDone && myJobs.size === 0) terminal = true;
            cb.onJobEnd?.({ jobId: jid, ok: ev.type === "job-done", line: ev.line ?? "" });
          } else if (ev.type === "tool-call") {
            cb.onJobStep?.(jid, ev.tool ?? "tool");
          } else if (ev.type === "job-progress") {
            cb.onJobProgress?.(jid, ev.line ?? "");
          } else if (ev.type === "commit") {
            // The one event the job shares with a turn verbatim: the panel re-reads the open
            // document on a commit, which is what makes the page the job wrote appear by itself.
            cb.onCommit(ev.sha);
          } else if (ev.type === "artifact" && ev.path) {
            cb.onArtifact?.({ workspace: ev.workspace ?? "", path: ev.path, focus: ev.focus === true, pin: ev.pin === true });
          } else if (ev.type === "open" && ev.path) {
            // A job may open a page too — it is the same ask, made by the same person, and the
            // only thing the job lane changes about it is that this connection has to recognise
            // the job as its own before it acts on it.
            cb.onOpen?.({ workspace: ev.workspace ?? "", path: ev.path, target: ev.target ?? "" });
          } else if (ev.type === "focus" && ev.workspace) {
            // …and a job may make a workspace: Create runs as one. The chat it was asked in is
            // still the chat that made the place.
            cb.onFocusWorkspace?.({ workspace: ev.workspace, name: ev.name ?? "" });
          }
          continue;
        }
        switch (ev.type) {
          // The worker's liveness ack — emitted the moment a turn is picked up (warm or cold), long
          // before the first model token. Flips the heartbeat "Starting agent" → "Working"
          // honestly: the agent IS working, it just hasn't produced output yet.
          case "turn-accepted":
            sawVisibleOutput = true;
            cb.onStatus?.("working");
            cb.onAccepted?.();
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
            if (ev.path) cb.onArtifact?.({ workspace: ev.workspace ?? "", path: ev.path, focus: ev.focus === true, pin: ev.pin === true });
            break;
          // SOMEBODY ASKED TO SEE SOMETHING (Vexa-ai/vexa#1586). The founder typed "open meeting
          // transcript"; the agent read the transcript and described it, because describing was
          // the only move it had. `open_page` is the move, and this is the half that honours it.
          //
          // Emitted only on a tool that ANSWERED yes — "no transcript for this meeting" comes back
          // to the model as a refusal and paints nothing here, so a turn can never say it opened
          // something the panel did not show. Forwarded like every other panel event: this reader
          // owns no state and the shell decides, because the strip belongs to the chat record.
          case "open":
            if (ev.path) cb.onOpen?.({ workspace: ev.workspace ?? "", path: ev.path, target: ev.target ?? "" });
            break;
          // A WORKSPACE THE TURN MADE (Vexa-ai/vexa#1603). Emitted by the harness off a successful
          // `workspace_new` and nothing else, so a chat can never take the focus of a workspace it
          // merely read. Forwarded like every other surface event: the focus set is the chat
          // record's and this reader owns none of it.
          case "focus":
            if (ev.workspace) cb.onFocusWorkspace?.({ workspace: ev.workspace, name: ev.name ?? "" });
            break;
          // THE TRANSCRIPT'S CHIPS (decision 35). Emitted by the harness off a successful
          // `transcript_terms` PUBLISH — the agent's second call, the one carrying `keep`. A bare
          // look-up emits nothing, so a turn that read the room without choosing anything paints
          // nothing on the person's screen.
          //
          // NOT counted as visible output: a Highlight turn is machinery and produces no prose, and
          // treating this as "the agent said something" would keep the "starting agent…" affordance
          // honest for the wrong turn.
          case "terms":
            if (ev.meeting && Array.isArray(ev.terms) && ev.terms.length) {
              cb.onTerms?.({ meeting: ev.meeting, cursor: ev.cursor ?? "", terms: ev.terms });
            }
            break;
          case "commit":
            terminal = true; cb.onCommit(ev.sha);
            break;
          case "rejected":
            terminal = true; cb.onRejected();
            break;
          case "turn-complete":
            // …unless this turn spawned a job. The turn IS over — that is why the job exists — but
            // the connection is now the job's, and calling it finished here would give up on
            // everything it has left to say. `RedisStreamReader` makes the same exception, in the
            // same two lines, on the server side.
            turnDone = true;
            if (typeof ev.steps === "number") cb.onSteps?.(ev.steps);
            if (myJobs.size === 0) terminal = true;
            break;
          case "done": {
            terminal = true;
            // `reason` distinguishes "the model failed" from "the turn stopped early" (F89). They
            // read identically in the old shape — both arrive as ok=false — and telling a person
            // "Model inference failed" when the model worked and the BUDGET ran out sends them
            // looking at the wrong thing.
            const reason = typeof ev.reason === "string" ? ev.reason : "";
            // THE SERVER'S OWN COUNT, on every done (Vexa-ai/vexa#1622) — including the ones that
            // finished, because a step count that only appears when something went wrong is a
            // count nobody can compare against.
            if (typeof ev.steps === "number") cb.onSteps?.(ev.steps);
            if (reason && cb.onTruncated) {
              sawVisibleOutput = true;
              // The act is passed on ONLY when it is whole. A half-record — a label with no
              // instruction — would draw a button that posts nothing, which is worse than the
              // silent stop it replaces.
              const act = ev.act && ev.act.label && ev.act.instruction
                ? { label: ev.act.label, instruction: ev.act.instruction } : undefined;
              cb.onTruncated(reason, ev.reply, { steps: ev.steps, budget: ev.budget, act });
            } else if (ev.ok === false) { sawVisibleOutput = true; cb.onModelFailure(ev.reply); }
            break;
          }
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
    // The hard cap answers "did the worker ever come back". A job is ALLOWED to take minutes — that
    // is what it is for — so while one is running the cap is not the question and giving up on it
    // would abandon work that is still happening.
    if (myJobs.size === 0 && now() - startedAt > hardTimeoutMs) break;
    cb.onStarting();  // still waiting on the worker — keep the pane honest between attempts
    cb.onStatus?.("reconnecting");
    await sleep(backoffMs);
  }

  cb.onStatus?.(null);  // turn ended (or gave up) — drop the live indicator
  return { sawVisibleOutput, terminal, aborted: signal.aborted, cursor: lastEventId };
}
