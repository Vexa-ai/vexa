/** inbox — everything submitted to a chat, held by the SERVER (Vexa-ai/vexa#1610).
 *
 *  The founder, dropping several Extend acts with their own instruction lines onto one page while a
 *  job ran: *"i drop new tasks to that chat, can i be sure everything submitted there is actually
 *  processed?"* One half of why the answer was no lived here, in the browser: a message typed
 *  mid-turn was queued in `localStorage` and sent when the turn ended (#1594). That is a queue with
 *  ONE reader, in ONE tab. Another device never saw it; a cleared browser never sent it; and nothing
 *  anywhere recorded that it had ever existed.
 *
 *  So the queue moved to the server. A submission is POSTed the moment it is made — mid-turn or not
 *  — onto the session's own inbox (`POST /api/chat/submit`), and what is still pending is READ BACK
 *  (`GET /api/chat/pending`). The browser keeps at most an UNSENT copy, across the POST itself, for
 *  a network gap, and clears it on the ack.
 *
 *  That is the whole shape, and it is why a reload, a second window and a swapped terminal container
 *  show the same pending list: none of them is remembering it.
 */
import type { ChatIntent } from "./chatIntent";
import { readFault, type Fault } from "./faults";
import type { JobRec } from "./jobs";

/** One thing this chat has submitted and its agent has not taken yet — the server's row, verbatim. */
export type InboxItem = {
  /** the in-topic stream id — the server's own name for this entry, and what ORDERS the queue */
  entry: string;
  /** the id the client minted at the press; `entry` when a submission carried none */
  id: string;
  /** the act, or "" for a sentence somebody typed */
  kind: string;
  /** the page (or meeting passage) the act names, or "" for a message */
  target: string;
  /** the person's own words, as they were submitted — never the composed prompt */
  display: string;
  /** when it was submitted (epoch seconds) */
  at: number;
  /** WHAT IS HOLDING IT, when something is (P18) — the runtime fault that stopped this chat's queue.
   *  Absent while the queue is moving; the server drops it the moment the worker takes anything. */
  blocked?: unknown;
};

/** `fault` — the same block once, for a view that wants a single banner rather than N rows. */
export type InboxView = { pending: InboxItem[]; cursor: string; fault?: Fault | null };

/** What the client sends. Deliberately the same fields a streamed turn sends: one composition path
 *  on the server means one set of arguments here. */
export type Submission = {
  id: string;
  session: string;
  prompt: string;
  active?: unknown;
  context?: unknown;
  scaffoldId?: string;
  intent?: ChatIntent;
  /** the person's own words, for the row a REFUSED submission draws (P18) — never sent: the server
   *  composes its own record of them from the prompt */
  display?: string;
};

/** How much of a person's sentence names its own queued row. A row is read in one line beside the
 *  step rows, so it is a label; the whole message would be a paragraph in the middle of a chat. */
export const QUEUED_LABEL_MAX = 60;

const EMPTY: InboxView = { pending: [], cursor: "" };

/** THE ONE UNSENT COPY. Per session, and only across the POST: written before the request, removed
 *  when the server has acknowledged it. It is not a queue — the server's inbox is the queue — it is
 *  the answer to "the network dropped between the press and the ack", which is the one gap the
 *  server cannot see. */
const outboxKey = (session: string) => `vexa.outbox.${session}`;

export function readOutbox(session: string): Submission[] {
  try {
    const raw = localStorage.getItem(outboxKey(session));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as Submission[]).filter((s) => s && s.id && s.prompt) : [];
  } catch {
    return [];
  }
}

function writeOutbox(session: string, items: Submission[]): void {
  try {
    if (items.length) localStorage.setItem(outboxKey(session), JSON.stringify(items));
    else localStorage.removeItem(outboxKey(session));
  } catch {
    /* a browser that refuses storage still submits — it just cannot survive a dropped POST */
  }
}

export function rememberUnsent(s: Submission): void {
  const kept = readOutbox(s.session).filter((x) => x.id !== s.id);
  writeOutbox(s.session, [...kept, s]);
}

export function forgetUnsent(session: string, id: string): void {
  writeOutbox(session, readOutbox(session).filter((x) => x.id !== id));
}

/** A submission id — the client's own name for this press, carried to the server and back so the
 *  row it draws and the row the server reports are the same row. */
export function newSubmissionId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function view(data: unknown): InboxView {
  const d = (data ?? {}) as { pending?: unknown; cursor?: unknown; fault?: unknown };
  const pending = Array.isArray(d.pending) ? (d.pending as InboxItem[]) : [];
  const fault = readFault(d.fault);
  return { pending, cursor: typeof d.cursor === "string" ? d.cursor : "", ...(fault ? { fault } : {}) };
}

/** Is anything on this list going to RUN? A queue whose every row is blocked has nothing to watch,
 *  and attaching to it would only wait out a timeout for a worker nobody started. */
export function runnable(items: InboxItem[]): InboxItem[] {
  return items.filter((i) => !readFault(i.blocked));
}

/** Put a submission on the server NOW, and return the server's view of what is queued.
 *
 *  Throws on a refusal or a network failure, with the unsent copy left in place: the caller is
 *  mid-composer and has a person in front of it, and a submission that silently did not happen is
 *  the exact defect this file exists to remove. */
/** THE SERVER REFUSED THE SUBMISSION — and, since P18, says which dependency failed. The unsent copy
 *  stays in the outbox, so a Retry re-sends exactly this submission under exactly this id (and the
 *  server withdraws any copy it still holds under that id first, so it runs once). */
export class SubmitRefused extends Error {
  readonly status: number;
  readonly fault: Fault | null;
  constructor(status: number, fault: Fault | null) {
    super(`Submit failed (${status})`);
    this.name = "SubmitRefused";
    this.status = status;
    this.fault = fault;
  }
}

export async function submitToInbox(s: Submission, fetchImpl: typeof fetch = fetch): Promise<InboxView> {
  rememberUnsent(s);
  const r = await fetchImpl("/api/chat/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: s.prompt, session: s.session, turn_id: s.id, active: s.active, context: s.context,
      ...(s.scaffoldId ? { scaffold_id: s.scaffoldId } : {}),
      ...(s.intent ? { intent: s.intent } : {}),
    }),
  });
  if (!r.ok) {
    let fault: Fault | null = null;
    try { fault = readFault(((await r.json()) as { fault?: unknown } | null)?.fault); } catch { /* not JSON */ }
    throw new SubmitRefused(r.status, fault);
  }
  forgetUnsent(s.session, s.id);
  return view(await r.json());
}

/** What this chat has submitted and its agent has not taken yet. Never throws: a chat that cannot
 *  read its inbox shows no queued rows, which is what it showed before one existed. */
export async function fetchPending(session: string, fetchImpl: typeof fetch = fetch): Promise<InboxView> {
  try {
    const r = await fetchImpl(`/api/chat/pending?session=${encodeURIComponent(session)}`);
    if (!r.ok) return EMPTY;
    return view(await r.json());
  } catch {
    return EMPTY;
  }
}

/** Re-send whatever a dropped POST left behind — called when the chat is idle and on load, so a
 *  network gap costs a moment and never a message. Returns the ids that made it.
 *
 *  `onRefused` (P18): a submission the SERVER refused with a typed fault is not a network gap, and
 *  saying nothing about it would leave a message that exists only in this browser's storage — the
 *  caller draws it as a blocked row, with the fault, so the person can see it and retry it. */
export async function flushOutbox(session: string, fetchImpl: typeof fetch = fetch,
                                  onRefused?: (s: Submission, fault: Fault) => void): Promise<string[]> {
  const sent: string[] = [];
  for (const s of readOutbox(session)) {
    try {
      await submitToInbox(s, fetchImpl);
      sent.push(s.id);
    } catch (e) {
      if (e instanceof SubmitRefused && e.fault) onRefused?.(s, e.fault);
      break;   // still no network, or still refused — leave the rest for the next idle moment
    }
  }
  return sent;
}

const label = (text: string): string => {
  const flat = String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
  const cp = [...flat];
  return cp.slice(0, QUEUED_LABEL_MAX).join("") + (cp.length > QUEUED_LABEL_MAX ? "…" : "");
};

/** The server's pending list as chat rows — one row per item, so the person can COUNT what is
 *  waiting. An act is named by its target (the same string its job will be named by); a message is
 *  named by the person's own words. */
export function inboxRows(items: InboxItem[]): JobRec[] {
  return items.map((i) => {
    const blocked = readFault(i.blocked);
    return {
      id: i.id || i.entry,
      kind: i.kind || "message",
      target: i.target || label(i.display),
      steps: 0,
      label: "",
      queued: true,
      inbox: true,
      noun: i.kind ? "job" : "queued",
      ...(blocked ? { blocked, display: i.display } : {}),
    };
  });
}

/** A SUBMISSION THE SERVER REFUSED, as a row (P18). It is this browser's — the server holds nothing
 *  for it (a refused dispatch withdraws its own entry) — so it is marked `outbox` and survives the
 *  reconcile below until a retry lands or the person dismisses it. One row per id: a second refusal
 *  of the same submission replaces the first rather than stacking. */
export function blockSubmission(jobs: JobRec[], row: { id: string; kind?: string; display: string }, fault: Fault): JobRec[] {
  const kind = row.kind || "message";
  return [...jobs.filter((j) => j.id !== row.id), {
    id: row.id, kind, target: label(row.display), steps: 0, label: "", queued: true,
    outbox: true, noun: row.kind ? "job" : "queued", blocked: fault, display: row.display,
  }];
}

/** THE ROWS THE SERVER OWNS REPLACE THE ROWS THE SERVER OWNS — and nothing else is touched.
 *
 *  A running job's row is this client's (it is watching that job's events); a queued row is the
 *  server's, because the server is the only thing that knows whether a worker has taken it yet.
 *  Reconciling rather than merging is the point: a row this browser drew optimistically at the press
 *  disappears when the server says the work has started, instead of lingering beside its own job. */
export function reconcileInbox(jobs: JobRec[], items: InboxItem[]): JobRec[] {
  return [...jobs.filter((j) => !j.inbox), ...inboxRows(items)];
}

/** THE QUEUED ROW A JOB HAS JUST TAKEN OVER. One act, one row: when `job-started` names a target,
 *  the row that was WAITING for that target has become the row that is running, so the waiting one
 *  goes. The FIRST match only — two acts queued on one page are two rows, and the second is still
 *  genuinely waiting. */
export function claimInboxRow(jobs: JobRec[], target: string): JobRec[] {
  const i = jobs.findIndex((j) => j.inbox && j.target === target);
  return i < 0 ? jobs : [...jobs.slice(0, i), ...jobs.slice(i + 1)];
}
