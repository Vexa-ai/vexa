/**
 * Session write-back — an authenticated bot's rotated browser session goes back through meeting-api.
 *
 * Google rotates session cookies during use, so on clean teardown the bot hands the live profile's
 * session files back for the next spawn to restore (#725: restore freshest → use → write back). The
 * bot never writes the session store itself: its userdata key is read-only. It PUTs the session
 * profile (`readSessionProfile` — the SESSION_PROFILE files of its own profile dir, regular files
 * only; the session-profile.v1 contract's `WritebackBody`) to meeting-api with the MeetingToken it
 * already holds, and meeting-api stores what it accepts: SESSION_PROFILE paths only, from the session
 * that is the live authenticated bot.
 *
 * The URL is the invocation's `sessionWritebackUrl` (invocation.v1), which meeting-api sends only in
 * authenticated mode, this session's uid already in it. The bot builds no URL of its own: an
 * invocation without the field gets no write-back.
 */
import { readSessionProfile } from '@vexa/remote-browser';
import type { Invocation } from './config.js';
import type { LifecycleSink } from './ports.js';

export interface WritebackResult {
  /** session profile files meeting-api accepted */
  sent: number;
  /** why nothing was sent, when nothing was */
  skipped?: string;
}

export interface WritebackOptions {
  fetchImpl?: typeof fetch;
  /** One attempt, bounded: teardown never waits on it longer than this. */
  timeoutMs?: number;
}

/**
 * PUT the session profile of `dataDir` to the invocation's `sessionWritebackUrl`. Resolves with what
 * was sent or why nothing was; rejects when meeting-api answers anything but 2xx (the caller logs it
 * as a warning — the durable copy then stays at the last restore).
 */
export async function writeBackSession(inv: Invocation, dataDir: string, opts: WritebackOptions = {}): Promise<WritebackResult> {
  const url = inv.sessionWritebackUrl;
  if (!url) return { sent: 0, skipped: 'the invocation names no session write-back URL' };
  if (!inv.token) return { sent: 0, skipped: 'the invocation carries no MeetingToken' };
  const files = readSessionProfile(dataDir);
  if (!files.length) return { sent: 0, skipped: 'the live profile holds no session files' };
  const body = JSON.stringify({ files: files.map((f) => ({ path: f.path, data: f.data.toString('base64') })) });
  const res = await (opts.fetchImpl ?? fetch)(url, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${inv.token}`, 'Content-Type': 'application/json' },
    body,
    signal: AbortSignal.timeout(opts.timeoutMs ?? 15_000),
  });
  const detail = await res.text().catch(() => '');
  if (!res.ok) {
    throw new Error(`meeting-api refused the session write-back: HTTP ${res.status} ${detail.slice(0, 200)}`.trim());
  }
  return { sent: files.length };
}

/**
 * Did this bot get past sign-in? Only then may its profile go back to the account: a bot that sat on
 * the platform's sign-in page (or a check) and never reached the meeting holds a signed-out cookie set,
 * and writing it back would overwrite the account's good one.
 *
 * The evidence is what the bot already reports on lifecycle.v1: `awaiting_admission` (it reached the
 * lobby as itself) or `active` (it was admitted). A bot a person helped in through the live view and
 * that was then admitted reported `active`, so it writes back — the case where a person signs in once
 * and the session is saved. A bot that ended while still `joining`, or in `needs_help` entered from
 * `joining`, reported neither.
 */
export interface SignInEvidence {
  /** reported `awaiting_admission` */
  lobbyReached: boolean;
  /** reported `active` */
  admitted: boolean;
}

/** The write-back decision as a pure function: a signed-in invocation that got past sign-in. */
export function shouldWriteBack(authenticated: boolean, evidence: SignInEvidence): boolean {
  return authenticated && (evidence.admitted || evidence.lobbyReached);
}

/** Records, from the lifecycle statuses the bot emits, whether it got past sign-in. */
export function createSignInEvidence(): { observe(status: string): void; evidence(): SignInEvidence } {
  const e: SignInEvidence = { lobbyReached: false, admitted: false };
  return {
    observe(status: string) {
      if (status === 'awaiting_admission') e.lobbyReached = true;
      else if (status === 'active') e.admitted = true;
    },
    evidence: () => ({ ...e }),
  };
}

/** The same sink, telling `onStatus` every status it carries (emit and the reachability-gated first emit). */
export function observeLifecycle(sink: LifecycleSink, onStatus: (status: string) => void): LifecycleSink {
  const seen = (status: unknown): void => { try { onStatus(String(status)); } catch { /* never in the way of a report */ } };
  return {
    emit(event) { seen(event.status); return sink.emit(event); },
    ...(sink.emitReachable
      ? { emitReachable: (event: Parameters<NonNullable<LifecycleSink['emitReachable']>>[0]) => { seen(event.status); return sink.emitReachable!(event); } }
      : {}),
  };
}
