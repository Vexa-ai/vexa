/**
 * v0.10.5 Pack G.1 (#272 issue 6) — structured-JSON logging discipline.
 *
 * WHY this exists:
 *   When a bot pod dies, its [Graceful Leave] shutdown logs vanish with
 *   the pod. The [Delayed Stop] (90 s) has time to capture them but
 *   doesn't (Pack G.2/G.3 wires that capture). Without structured JSON
 *   on the bot side, every recording-loss bug becomes a "we don't know
 *   why" post-mortem because the captured logs are unparseable
 *   prefix-tagged free text.
 *
 *   Pack G.1 closes the discipline gap on the bot side: every log line
 *   becomes a single-line JSON object with a stable schema that
 *   {meeting_id, session_uid, platform, ...} ride along with the
 *   message. Operators can grep+jq the captured logs deterministically.
 *
 * DESIGN — minimal-disruption shim:
 *   * `logJSON({level, msg, ...fields})` — direct structured emit.
 *   * `setLogContext({meeting_id, session_uid, platform, container_name})` —
 *     once-per-bot context populated from BotConfig in runBot(). Every
 *     subsequent log line carries these fields automatically.
 *   * `log(message: string)` (re-exported through utils.ts) — back-compat
 *     wrapper. Existing call sites stay verbatim. The wrapper parses
 *     leading `[Prefix]` tokens (e.g. `[Graceful Leave]`, `[Recording]`,
 *     `[VoiceAgent]`) into a `subsystem` field so the structured event
 *     surfaces the same routing information that pre-G.1 lived inside
 *     the message. Multiple chained prefixes are concatenated
 *     (`[VideoRecording] Screen capture started` →
 *     `subsystem: "VideoRecording"`).
 *
 * PRINCIPLE FILTER:
 *   * Bot-side defensive selectors for Zoom/Meet/Teams DOM are still
 *     allowed (per stage rules); this is a logging-discipline change,
 *     orthogonal to the no-fallbacks rule on internal subsystems.
 *   * stderr is NOT used — single-stream stdout matches the K8s
 *     `kubectl logs` capture model (Pack G.2 reads container stdout).
 *
 * REGISTRY CHECK: BOT_LOGS_STRUCTURED_JSON (grep, modes:[lite]) —
 *   asserts that this module exists and that utils.ts log() routes
 *   through logJSON.
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogContext {
  meeting_id?: number | string;
  session_uid?: string;
  platform?: string;
  container_name?: string;
  connection_id?: string;
  bot_name?: string;
}

let logContext: LogContext = {};

// v0.10.5.3 Pack O — ring buffer of last N structured-JSON log lines.
// On exit, performGracefulLeave fetches getLogBuffer() and includes the
// contents in the exit-callback's payload so meeting-api can persist
// them into meetings.data.bot_logs JSONB. With Pack G.2 deferred (k8s-side
// stdout capture is more involved), this in-process ring is the minimum-
// viable forensic instrumentation: when a bot crashes, the operator gets
// the last ~200 structured log lines via the meeting row even though the
// pod's stdout died with it.
//
// Cap: 200 lines × ~500 bytes typical = ~100 KB max in-memory. Trimmed
// further to 50 KB on the meeting-api side before persisting.
const LOG_BUFFER_CAP = 200;
const logBuffer: string[] = [];

export function getLogBuffer(): readonly string[] {
  return logBuffer;
}

export function clearLogBuffer(): void {
  logBuffer.length = 0;
}

/**
 * Set the per-bot log context. Call once from runBot() with values
 * derived from BotConfig (meeting_id, connectionId, platform). Subsequent
 * logJSON() calls automatically carry these fields.
 */
export function setLogContext(ctx: Partial<LogContext>): void {
  logContext = { ...logContext, ...ctx };
}

export function getLogContext(): Readonly<LogContext> {
  return logContext;
}

const KNOWN_PREFIX_RE = /^\s*\[([^\]]+)\]\s*/;

/**
 * Parse a leading `[Prefix]` from a message into {subsystem, rest}.
 * Returns {subsystem: undefined, rest: msg} when the message has no
 * recognizable prefix. Multiple chained prefixes are joined with `:`
 * (e.g. `[Vexa] [ZOOM_OBSERVE] tick=1` → subsystem `Vexa:ZOOM_OBSERVE`).
 */
function extractSubsystem(msg: string): { subsystem?: string; rest: string } {
  const parts: string[] = [];
  let rest = msg;
  // Limit to 4 chained prefixes — anything more is unusual and we'd rather
  // truncate than slow-loop on pathological input.
  for (let i = 0; i < 4; i++) {
    const m = rest.match(KNOWN_PREFIX_RE);
    if (!m) break;
    parts.push(m[1].trim());
    rest = rest.slice(m[0].length);
  }
  if (parts.length === 0) return { subsystem: undefined, rest };
  return { subsystem: parts.join(":"), rest };
}

/**
 * Emit a single-line JSON log object to stdout.
 *
 * Schema:
 *   {
 *     ts:           ISO8601 string,
 *     level:        "debug" | "info" | "warn" | "error",
 *     msg:          string,
 *     // — context (auto-injected from setLogContext) —
 *     meeting_id?:  number | string,
 *     session_uid?: string,
 *     platform?:    string,
 *     container_name?: string,
 *     connection_id?: string,
 *     // — message-derived —
 *     subsystem?:   string,    // parsed from [Prefix] tokens
 *     // — caller-supplied —
 *     ...fields
 *   }
 *
 * Failure mode: if JSON.stringify throws on a circular reference in
 * extra fields, falls through to a plain-text emit so the line is never
 * lost. The error is itself logged as a follow-up structured record so
 * the operator sees the surrogate.
 */
export function logJSON(record: {
  level?: LogLevel;
  msg: string;
  [k: string]: unknown;
}): void {
  const { level = "info", msg, ...extra } = record;
  const { subsystem, rest } = extractSubsystem(typeof msg === "string" ? msg : String(msg));
  const out: Record<string, unknown> = {
    ts: new Date().toISOString(),
    level,
    ...logContext,
    ...(subsystem ? { subsystem } : {}),
    msg: rest,
    ...extra,
  };
  let line: string;
  try {
    line = JSON.stringify(out);
  } catch (err) {
    // Surrogate path: emit a minimal object describing the stringify failure
    // and the original message so operators still get the signal.
    const surrogate = {
      ts: new Date().toISOString(),
      level: "warn",
      ...logContext,
      msg: `logJSON: JSON.stringify failed (${(err as Error)?.message ?? String(err)}); raw msg follows`,
      raw_msg: rest,
      subsystem,
    };
    try {
      line = JSON.stringify(surrogate);
    } catch {
      // Worst-case: emit plain text so the line is never silently lost.
      // eslint-disable-next-line no-console
      console.log(`[BotCore] logJSON serialize failure: ${rest}`);
      return;
    }
  }
  // eslint-disable-next-line no-console
  console.log(line);
  // v0.10.5.3 Pack O: push to ring buffer for exit-callback flush.
  logBuffer.push(line);
  if (logBuffer.length > LOG_BUFFER_CAP) {
    logBuffer.shift();
  }
}
