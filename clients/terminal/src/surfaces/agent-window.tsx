"use client";
/** agent-window — the shared agent chat engine. One vertically-stacked window (NO horizontal split):
 *  optional entity strip on top · the conversation (a turn timeline that makes the agent's operations
 *  visible — read/search/edit/git/web steps with live status, not just final text) · the composer ·
 *  proposed actions directly under the input. The right-rail chat and the `meeting` copilot render
 *  through this, so they look and behave like one product. */
import { ApprovalCard, type PrivateApproval } from "./approvalCard";
import { workspaceLabel } from "../ui-kit/wsLinks";
import { type CSSProperties, type ReactNode, type RefObject, useEffect, useState } from "react";
import { Badge, EntityChip, Icon, SourceList, extractSources, type Source } from "../ui-kit";
import { Markdown } from "../ui-kit/Markdown";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { MeetingChip, meetingNotePath } from "../ui-kit/MeetingChip";
import { OPEN_ENTITY_EVENT } from "../platform";
import { useFeature } from "../platform/features";
import { splitBecause } from "./because";
import { faultHeadline, faultSummary, faultTone, isSessionConflict, isTransientFault, type Fault } from "./faults";
import { UsageLimitCard, isUsageLimit } from "./usage/Usage";
import { MessageAttachments, type MessageAttachment } from "./attachments";
import "./agentWindow.css";
import { proposalButtons, proposalQuestion, type DecidedProposal, type ProposalChoice } from "./targetProposal";

// ── the turn model ────────────────────────────────────────────────────────────────
/** `held`: the call ended without effect, kept for the person's approval (the privacy hold, D6). */
export type OpStatus = "running" | "done" | "error" | "held";
export interface Op { icon: string; label: string; status: OpStatus; file?: string; wrote?: boolean;   // icon ∈ ui-kit; file = workspace doc the op touched
  /** the stream's id for the call, pairing its result with this step */
  callId?: string }
/** The live phase of an in-flight agent turn (see chatStream `ChatPhase`), plus when it began so the UI
 *  can tick an elapsed-seconds counter. Rendered as a verbose status line so the pane never looks frozen. */
export type TurnPhase = "connecting" | "working" | "reconnecting" | "stalled" | "queued";
/** `label` — the server's own sentence for a `queued` turn ("Finishing the meeting recap — your
 *  message runs next."). */
export interface TurnStatus { phase: TurnPhase; since: number; label?: string }
/** THE TURN RAN OUT OF BUDGET (Vexa-ai/vexa#1622) — the harness's own words for how far it got, and
 *  the act it offers. `line` is `done.reason` ("stopped at the tool-call budget after 40 of 40
 *  steps"); `act` is present only when there is something to continue.
 *
 *  A FIELD, not prose appended to `t.text`, which is what F89 did. Text cannot carry a button, and a
 *  turn that stops silently is exactly the failure this replaces — the founder re-typed the same
 *  instruction into three dead turns because the chat showed a finished one each time. */
export interface TurnStopped { line: string; act?: { label: string; instruction: string } }
/** THE TURN FAILED, AND THE CHAT KNOWS WHO FAILED IT (P18) — the runtime that would not start the
 *  agent, the model provider that refused the turn. A field rendered as its own block, never prose
 *  appended to the reply: "Internal Server Error" in the agent's bubble is exactly what the founder
 *  read for a runtime that had refused his agent, and it named nobody. `retry` draws the control
 *  that sends the same words again. */
export interface TurnFault { fault: Fault; retry?: boolean }
/** WHO SENT A TURN THAT CAME FROM ANOTHER AGENT (ADR-0046 decision 13) — the sending person, the
 *  thread an answer goes back to, and the label the sender is shown under. */
export interface FromAgent { uid: string; thread: string; label: string }
/** Asks the shell to open one of the reader's chats by its thread id. */
export const OPEN_CHAT_EVENT = "vexa:open-chat";
export type Turn =
  | { id: string; role: "user"; text: string; fromAgent?: FromAgent;
      /** when it was said, epoch seconds (the history's `at`; set on send) — places a shared
       *  chat's lines between the turns */
      at?: number;
      /** the files the message carried — drawn as thumbnails/chips above the bubble, never as text */
      attachments?: MessageAttachment[] }
  | { id: string; role: "agent"; text: string; ops: Op[]; commit?: string; rejected?: string; status?: TurnStatus | null;
      at?: number;
      /** the meeting's signals woke the agent for this turn (ADR transcript signals): drawn with a
       *  small "From the meeting" marker, the composed wake prompt never shown */
      fromMeeting?: boolean;
      /** what woke it: the room's chat, or a call */
      wokeBy?: "chat" | "call";
      /** the SERVER's step count for this turn (Vexa-ai/vexa#1622). Absent on a deployment one
       *  release behind, where the op line falls back to counting what this browser saw. */
      steps?: number; stopped?: TurnStopped; failed?: TurnFault;
      /** how many messages of older history the turn compacted to fit the model's context — a
       *  muted note in the activity line, never part of the reply */
      compacted?: number;
      /** Structured sources from the stream, when the server sends them (rendered as a SourceList). */
      sources?: Source[];
      /** the agent ASKED to move this chat's writes (UI decision 6b) — Switch / Keep, the person's call */
      proposal?: DecidedProposal;
      /** a write held because the session read private sources: approved by the person's click */
      approval?: PrivateApproval }
  | { id: string; role: "insight"; t?: string; text: string };

const PHASE_LABEL: Record<TurnPhase, string> = {
  connecting: "Starting agent",
  working: "Working",
  reconnecting: "Reconnecting",
  stalled: "Connection stalled — retrying",
  queued: "Your message runs next",
};

/** A live "what's happening" line for the in-flight turn — spinner + phase + elapsed seconds, self-ticking
 *  so a long think / tool run / reconnect reads as ALIVE, not stale. Reconnect/stall use an alert color. */
function StatusLine({ status }: { status: TurnStatus }) {
  const [, force] = useState(0);
  useEffect(() => { const t = setInterval(() => force((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const secs = Math.max(0, Math.floor((Date.now() - status.since) / 1000));
  const alert = status.phase === "reconnecting" || status.phase === "stalled";
  // F66 (5): a turn can genuinely think for a long time. Past 30s of one phase the line says so in
  // words — a spinner alone reads as hung, and "still working" is the difference between a product
  // that is slow and a product that is broken.
  const queued = status.phase === "queued";
  const quiet = !alert && !queued && secs >= 30;
  const color = alert ? "var(--accent)" : "var(--t3)";
  return (
    <div className="mt-1 t-xs f-mono aw-tone" style={{ display: "flex", alignItems: "center", gap: 8, "--aw-tone": color } as CSSProperties}>
      <span className="vx-op-spin r-full aw-spin" />
      <span data-turn-status>{queued ? (status.label || PHASE_LABEL.queued) : quiet ? "still working" : PHASE_LABEL[status.phase]}{secs >= 2 ? ` · ${secs}s` : ""}{alert || queued ? "" : "…"}</span>
    </div>
  );
}

export const opIcon: Record<string, string> = { read: "file", search: "search", edit: "edit", write: "file", git: "git", web: "web", tool: "zap" };

/** render [[wikilinks]] in agent/insight prose as accented spans (click wiring lives in the entity rail) */
function linkify(text: string): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((p, i) => (p.startsWith("[[") ? <span key={i} className="c-info">{p}</span> : <span key={i}>{p}</span>));
}

/** A finished turn's text, with its SOURCES as a citation list (guidelines §4.17): the structured
 *  ones when the stream sent them, else a "Sources" list lifted out of the prose — so the raw
 *  bullet list with every date written twice becomes one row per source, date once. */
/** An agent turn's text. With `rooms` on, a leading `_because …_` is the room agent's reason line:
 *  shown quietly above the act (R1 2.2), not as italic prose inside it. */
function TurnText({ text, sources, streaming }: { text: string; sources?: Source[]; streaming: boolean }) {
  const rooms = useFeature("rooms");
  const { because, body } = rooms ? splitBecause(text) : { because: null, body: text };
  return <>
    {because && <p className="vx-because" data-because="">{because}</p>}
    {streaming ? <Markdown>{body}</Markdown> : <SettledText text={body} sources={sources} />}
  </>;
}

function SettledText({ text, sources }: { text: string; sources?: Source[] }) {
  const lifted = extractSources(text);
  const list = sources?.length ? sources : lifted.sources;
  const body = sources?.length ? (lifted.sources.length ? lifted.body : text) : lifted.body;
  return <>
    <MdxDoc>{body}</MdxDoc>
    {list.length > 0 && <section aria-label="Sources" className="vx-turn-sources"><h3 className="vx-turn-sources-title">Sources</h3><SourceList sources={list} /></section>}
  </>;
}

// ── one operation step (the "what's in works" line) ──────────────────────────────
/** A FINISHED STEP READS AS DONE (founder 2026-10-16: "Working · 1 step" stayed under a finished
 *  answer). The verb goes to the past tense; the generic "Working" becomes "Done". */
const PAST: [RegExp, string][] = [[/^Working\b/, "Done"], [/^Looking through your files\b/, "Looked through your files"], [/^Reading the web\b/, "Read the web"], [/^Reading\b/, "Read"], [/^Searching\b/, "Searched"],
  [/^Writing\b/, "Wrote"], [/^Committing\b/, "Committed"], [/^Browsing\b/, "Browsed"], [/^Running\b/, "Ran"],
  [/^Loading skill\b/, "Loaded skill"]];
export function doneLabel(label: string): string {
  for (const [re, past] of PAST) if (re.test(label)) return label.replace(re, past);
  return label;
}

/** A STEP SAYS HOW IT ENDED (founder 2026-10-18: "✓ Wrote · 7 steps" over a reply that said
 *  "Nothing was written. The refactored README is held"). A write the privacy hold kept reads
 *  "Held for approval", a refused or failed write "Not written" — never the past tense of a write
 *  that did not happen. Other steps keep their own words. */
const WRITE_VERB = /^(Writing|Wrote)\b/;
export function opLabel(op: Pick<Op, "label" | "status" | "wrote">): string {
  const isWrite = !!op.wrote || WRITE_VERB.test(op.label);
  if (op.status === "held") return WRITE_VERB.test(op.label) ? op.label.replace(WRITE_VERB, "Held for approval") : `Held for approval · ${op.label}`;
  if (op.status === "error" && isWrite) return WRITE_VERB.test(op.label) ? op.label.replace(WRITE_VERB, "Not written") : `Not written · ${op.label}`;
  return op.status === "done" ? doneLabel(op.label) : op.label;
}

/** THE STEP THE TURN'S LINE SHOWS. While it runs, the current one. Once it has settled, a write that
 *  was held — else one that failed — outranks whatever ran after it: the line under an answer must
 *  not say "Wrote" when the one write it attempted was kept back. */
export function headerOp(ops: Op[]): Op {
  const last = ops[ops.length - 1];
  if (ops.some((o) => o.status === "running")) return last;
  const latest = (pick: (o: Op) => boolean) => [...ops].reverse().find(pick);
  return latest((o) => o.status === "held")
    ?? latest((o) => o.status === "error" && (!!o.wrote || WRITE_VERB.test(o.label)))
    ?? last;
}

/** THE FILES AN ANSWER IS ABOUT (founder 2026-10-16: a "README.md" chip under an answer that never
 *  mentioned it). A file the turn wrote gets a chip only when the answer names it — by file name, or
 *  by its name without the extension; a README touched on the side is not shown. */
export function chipFiles(ops: { file?: string; wrote?: boolean; status?: OpStatus }[], text: string | undefined): string[] {
  const said = (text ?? "").toLowerCase();
  // a write that was held or failed made no file (held-label-rooms-1018)
  const files = [...new Set(ops.filter((o) => o.wrote && o.file && o.status !== "held" && o.status !== "error").map((o) => o.file as string))];
  return files.filter((f) => {
    const base = (f.split("/").filter(Boolean).pop() ?? f).toLowerCase();
    const stem = base.replace(/\.[a-z0-9]+$/, "");
    return !!said && (said.includes(base) || (stem.length >= 3 && new RegExp(`\\b${stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(said)));
  });
}

/** AN ATTACHMENT BELONGS TO THE MESSAGE THAT CARRIED IT, or it is not shown (founder 2026-10-16: a
 *  screenshot thumbnail floated at the bottom right under no message). A message read back from history
 *  with no words of the person's (a hidden, composed or machinery turn) draws no thumbnails; one this
 *  browser just sent (`u-…`) keeps them even with no words, because the person is looking at it. */
export function shownAttachments(t: { id: string; text?: string; attachments?: MessageAttachment[] }): MessageAttachment[] {
  const items = t.attachments ?? [];
  if (!items.length) return [];
  return (t.text ?? "").trim() || t.id.startsWith("u-") ? items : [];
}

function OpRow({ op }: { op: Op }) {
  const running = op.status === "running";
  const color = op.status === "error" ? "var(--t3)" : op.status === "held" ? "var(--warning-text)"
    : op.status === "done" ? "var(--green)" : "var(--accent)";
  return (
    <div className="f-mono t-xs lh-snug aw-op" data-running={running ? "" : undefined} data-op-status={op.status} style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: "0 1 auto" }}>
      <span className="aw-tone" style={{ width: 13, flex: "none", display: "inline-flex", alignItems: "center", justifyContent: "center", "--aw-tone": color } as CSSProperties}>
        {op.status === "done" ? <Icon name="check" size={13} />
          : op.status === "error" ? <Icon name="x" size={13} />
          : op.status === "held" ? <Icon name="shield" size={13} />
          : <span className="vx-op-spin r-full aw-spin" />}
      </span>
      <span className="aw-glyph" data-tone="muted"><Icon name={op.icon} size={12} /></span>
      <span style={{ minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{opLabel(op)}</span>
    </div>
  );
}

// ── files the turn created/edited — ACTIONABLE chips: click opens the doc in the pages panel ──
function FileChip({ path }: { path: string }) {
  // A meeting's notes the turn wrote is named as the MEETING — its title and date — never as the
  // file (founder, 2026-10-11: "a special label for a meeting alongside people companies etc").
  if (meetingNotePath(path)) return <MeetingChip path={path} />;
  const name = path.split("/").filter(Boolean).pop() ?? path;
  // A page the turn wrote is a reference you can open: the entity chip (guidelines §4.5).
  return <EntityChip kind="doc" title={path} onOpen={() => window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path } }))}>{name}</EntityChip>;
}

// ── the act a stopped turn offers — one control, in the bubble it belongs to (Vexa-ai/vexa#1622) ──
function StoppedLine({ stopped, onContinue }: { stopped: TurnStopped; onContinue?: () => void }) {
  return (
    <div className="mt-2" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      <span className="t-xs c-2 f-mono bg-3 bd r-md pt-0_5 pr-2 pb-0_5 pl-2" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span className="aw-glyph" data-tone="accent"><Icon name="zap" size={12} /></span>{stopped.line}
      </span>
      {stopped.act && onContinue && (
        <button
          data-continue-act
          onClick={onContinue}
          title={stopped.act.instruction}
          className="t-xs f-mono c-info bg-info-tint bd-none r-md pt-0_5 pr-2 pb-0_5 pl-2" style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          {stopped.act.label}
        </button>
      )}
    </div>
  );
}

/** A PREVIOUS REPLY IS STILL RUNNING (app.dev 2026-10-15) — one calm line and the one control that
 *  helps. "Stop it" asks the server to kill the old turn; once it has taken that, the line says so
 *  and offers the message that was refused, through the same resend path as Retry. No status code,
 *  no store vocabulary, no "details": there is nothing behind this line a person needs. */
function StillRunning({ failed, onRetry, onStop }: { failed: TurnFault; onRetry?: () => void; onStop: () => Promise<boolean> }) {
  const [state, setState] = useState<"running" | "stopping" | "stopped" | "refused">("running");
  const press = async () => {
    setState("stopping");
    setState((await onStop()) ? "stopped" : "refused");
  };
  const f = failed.fault;
  return (
    <div role="status" data-fault-source={f.source} data-fault-kind={f.kind} data-fault-tone="neutral"
      data-still-running={state} className="aw-fault">
      <div className="aw-fault-line">
        {state === "stopped" ? (
          <>
            <span data-fault-summary className="aw-fault-summary">
              {failed.retry && onRetry ? "Stopping the previous reply —" : "Stopping the previous reply — send your message again in a moment."}
            </span>
            {failed.retry && onRetry && (
              <button type="button" data-fault-resend onClick={onRetry} className="aw-fault-retry">Send again</button>
            )}
          </>
        ) : (
          <>
            <span data-fault-summary className="aw-fault-summary">
              {state === "refused" ? "Couldn't stop the previous reply just now —" : "A previous reply is still running —"}
            </span>
            <button type="button" data-fault-stop onClick={() => void press()} disabled={state === "stopping"}
              className="aw-fault-retry">Stop it</button>
          </>
        )}
      </div>
    </div>
  );
}

/** The server's detail as a sentence: capitalised, and closed with a full stop unless it has one. */
const sentence = (d: string) => {
  const t = d.charAt(0).toUpperCase() + d.slice(1);
  return /[.!?…]$/.test(t) ? t : `${t}.`;
};

// ── a typed fault — one calm line; WHO failed · what kind · the status behind "details" (P18) ──
// The founder, 2026-10-13: "UPDATE UI SO THAT WE DO NOT HAVE RED DANGEROUS THINGS HERE". A
// one-second gateway 503 used to draw a red-bordered box headed "Agent service · unavailable (503)".
// The chat now retries that kind of fault itself (`chatStream`), so this renders only when it could
// not get through — and then as a muted sentence with a Retry, the service name and status code a
// press away. Red is the danger token's, kept for a security refusal (`faultTone`).
export function FaultBlock({ failed, onRetry, onStop }: {
  failed: TurnFault; onRetry?: () => void;
  /** STOP THE TURN STILL RUNNING in this conversation (`POST /api/chat/stop`) — true once the server
   *  took the request. Absent = a surface that cannot stop a turn, and no control is drawn. */
  onStop?: () => Promise<boolean>;
}) {
  const f = failed.fault;
  // A REFUSAL THAT A RUNNING TURN CAUSED (agent-api's 409) is not a fault to retry: it needs the old
  // turn stopped first. The history route's after-the-fact conflict ("the last reply was not saved")
  // names no action and carries no retry, so it stays an ordinary block.
  if (onStop && isSessionConflict(f) && (f.action === "stop" || failed.retry)) {
    return <StillRunning failed={failed} onRetry={onRetry} onStop={onStop} />;
  }
  // A FULL USAGE WINDOW (`usage`): the limit card — when it is back, "+ extra usage", "Wait"
  if (isUsageLimit(f)) return <UsageLimitCard detail={f.detail ?? ""} onRetry={failed.retry ? onRetry : undefined} />;
  const transient = isTransientFault(f);
  // A fault that is not transient leads with the server's own sentence, so it is not said twice.
  const leadsWithDetail = !transient && !!f.detail;
  return (
    <div role="alert" data-fault-source={f.source} data-fault-kind={f.kind} data-fault-tone={faultTone(f)}
      className="aw-fault">
      <div className="aw-fault-line">
        <span data-fault-summary data-fault-detail={leadsWithDetail ? "" : undefined} className="aw-fault-summary">{faultSummary(f)}</span>
        {failed.retry && onRetry && (
          <button type="button" data-fault-retry onClick={onRetry} className="aw-fault-retry">Retry</button>
        )}
        <details data-fault-details className="aw-fault-details">
          <summary>details</summary>
          <div data-fault-headline>{faultHeadline(f)}</div>
          {f.detail && !leadsWithDetail && <div data-fault-detail>{sentence(f.detail)}</div>}
          {transient && f.remedy && <div data-fault-remedy>{f.remedy}</div>}
        </details>
      </div>
      {!transient && f.remedy && <div data-fault-remedy className="aw-fault-remedy">{f.remedy}</div>}
    </div>
  );
}

// ── the conversation: a timeline of user bubbles · agent turns (ops + text) · insights ──
/** A line the agent says before anyone has written — drawn EXACTLY as an agent turn is (founder
 *  2026-10-15: the pre-meeting brief prompt showed as plain centred text, not as the agent speaking). */
export function AgentLine({ text }: { text: string }) {
  return (
    <div className="mb-4" data-agent-line="">
      <div className="t-sm c-1 lh-normal" style={{ maxWidth: 680 }}><Markdown>{text}</Markdown></div>
    </div>
  );
}

/** "Write to OeNB × Vexa pilot instead of biz? — <reason>" with [Switch] [Keep biz] (UI decision 6b).
 *  Nothing moves until the person clicks; once they have, the line says what they chose. */
function ProposalLine({ proposal, onDecide }: { proposal: DecidedProposal; onDecide?: (c: ProposalChoice) => void }) {
  const b = proposalButtons(proposal);
  return (
    <div className="aw-proposal" data-target-proposal={proposal.workspace} data-decided={proposal.decided ?? undefined}>
      <span className="aw-proposal-q">{proposalQuestion(proposal)}{proposal.reason ? ` — ${proposal.reason}` : ""}</span>
      {proposal.decided
        ? <span className="aw-proposal-done">{proposal.decided === "switch" ? `Switched to ${proposal.name}` : b.keep}</span>
        : onDecide && (
          <span className="aw-proposal-acts">
            <button data-proposal-act="switch" className="vx-btn" data-variant="primary" onClick={() => onDecide("switch")}>{b.switch}</button>
            <button data-proposal-act="keep" className="vx-btn" data-variant="secondary" onClick={() => onDecide("keep")}>{b.keep}</button>
          </span>
        )}
    </div>
  );
}

export function Conversation({ turns, busy, empty, onContinue, onStopRunning, onDecide }: {
  turns: Turn[]; busy?: boolean; empty?: ReactNode;
  /** WHERE THE CONTINUE PRESS GOES (Vexa-ai/vexa#1622). Passed in rather than dispatched from here:
   *  the shell owns the act the stopped turn was running, and a same-target act needs that intent —
   *  this component knows a turn, not a target. Absent = no control is drawn, so a surface that
   *  cannot resubmit never shows a button that would do nothing. */
  onContinue?: (turn: Extract<Turn, { role: "agent" }>) => void;
  /** STOP THE TURN STILL RUNNING in this conversation, from a turn that was refused because of it
   *  (agent-api's 409). True once the server took the request. Absent = no Stop control. */
  onStopRunning?: (turn: Extract<Turn, { role: "agent" }>) => Promise<boolean>;
  /** The person's answer to a write-target proposal (UI decision 6b). Absent = no buttons. */
  onDecide?: (turn: Extract<Turn, { role: "agent" }>, choice: ProposalChoice) => void;
}) {
  if (turns.length === 0 && empty) return <>{empty}</>;
  return (
    <>
      {turns.map((t, i) => {
        if (t.role === "user") {
          const queued = t.id.startsWith("q-");   // typed mid-turn; fires when the current turn ends
          return <div key={t.id} className="mb-4">
            {shownAttachments(t).length ? <MessageAttachments items={shownAttachments(t)} /> : null}
            {t.text ? <div className="aw-bubble" data-queued={queued ? "" : undefined}>{t.text}</div> : null}
            {queued && <div className="t-xs c-3 f-mono mt-0_5" style={{ textAlign: "right" }}>queued</div>}
          </div>;
        }
        if (t.role === "insight") return (
          <div key={t.id} className="mb-3" style={{ display: "flex", gap: 10 }}>
            <span className="aw-glyph" data-tone="accent" data-nudge=""><Icon name="spark" size={15} /></span>
            <div>{t.t && <span className="t-xs c-3 f-mono">{t.t}</span>}
              <div className="t-sm c-1 lh-normal mt-0_5">{linkify(t.text)}</div></div>
          </div>
        );
        const last = i === turns.length - 1;
        return (
          <div key={t.id} className="mb-4">
            {t.ops.length > 0 && (
              // ONE line, updated in place: the CURRENT (last) op + a step count — a long tool run
              // must not grow the transcript vertically (founder ruling 2026-08-22).
              // A long step label ellipsizes; it never widens the conversation (guidelines §3.4).
              <div className="mt-0 mr-0 mb-2 ml-1" style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <OpRow op={headerOp(t.ops)} />
                {/* F66: the count ticks from the FIRST step. It used to appear only at two, so the
                    one number that proves a long turn is moving was hidden exactly when the reader
                    starts looking for it.
                    …and the SERVER's count wins once it arrives (Vexa-ai/vexa#1622): this browser
                    counts the `tool-call` events IT saw, which is one event short of the truth on
                    any turn it attached to mid-flight, and the settled number is the one the budget
                    was measured against. */}
                {(() => {
                  const n = typeof t.steps === "number" ? t.steps : t.ops.length;
                  return <span className="vx-turn-steps">
                    · {n} step{n === 1 ? "" : "s"}
                  </span>;
                })()}
                {t.compacted ? <CompactedNote /> : null}
              </div>
            )}
            {t.ops.length === 0 && t.compacted ? (
              <div className="aw-compacted-alone"><CompactedNote /></div>
            ) : null}
            {t.text && <div className="t-sm c-1 lh-normal" style={{ maxWidth: 680 }}>
              {/* Mintlify-grade rendering in the OUTPUT too: finished turns compile as MDX (Note/Card/
                  Steps/Tabs + wikilinks, safe plain-markdown fallback); the still-streaming turn uses the
                  light parser and upgrades on completion. */}
              <TurnText text={t.text} sources={t.sources} streaming={!!(busy && last)} />
            </div>}
            {(() => {
              // every file the turn WROTE, deduped — the output's actionable surface
              const files = chipFiles(t.ops, t.text);
              return files.length > 0 && (
                <div className="vx-turn-meta vx-refs">
                  {files.map((f) => <FileChip key={f} path={f} />)}
                </div>
              );
            })()}
            {busy && last && (t.status
              ? <StatusLine status={t.status} />
              : (!t.text && !t.failed && <div className="t-sm c-3">…</div>))}
            {t.commit && (
              <div className="vx-turn-meta"><Badge tone="success"><Icon name="git" size={12} />Committed · <code className="vx-tabular">{t.commit.slice(0, 7)}</code></Badge></div>
            )}
            {t.rejected && (
              <div className="vx-turn-meta"><Badge tone="danger"><Icon name="x" size={12} />{t.rejected}</Badge></div>
            )}
            {t.approval && <ApprovalCard a={t.approval} workspaceName={t.approval.name || workspaceLabel(t.approval.workspace)} />}
            {t.proposal && <ProposalLine proposal={t.proposal} onDecide={onDecide && ((c) => onDecide(t, c))} />}
            {t.stopped && <StoppedLine stopped={t.stopped} onContinue={onContinue && (() => onContinue(t))} />}
            {t.failed && <FaultBlock failed={t.failed} onRetry={onContinue && (() => onContinue(t))}
              onStop={onStopRunning && (() => onStopRunning(t))} />}
          </div>
        );
      })}
    </>
  );
}

/** THE HISTORY WAS COMPACTED (founder 2026-10-10): a quiet note in the activity line, never the
 *  reply and never the stop line — the turn answered; older history was shortened to fit. */
function CompactedNote() {
  return (
    <span data-compacted title="Older parts of this chat were shortened to fit the model's context; your messages were kept." className="aw-compacted">
      · older context compacted
    </span>
  );
}

// ── the stacked shell: top strip · scrolling conversation · composer · actions-under-input ──
export function AgentWindow({ top, scrollRef, children, composer, actions }: {
  top?: ReactNode; scrollRef?: RefObject<HTMLDivElement | null>; children: ReactNode; composer: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="bg-1" style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0 }}>
      {top}
      <div ref={scrollRef} className="pt-4 pr-5 pb-4 pl-5" style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>{children}</div>
      {(composer || actions) && <div className="bd-t pt-3 pr-5 pb-3 pl-5" style={{ flex: "none" }}>
        <div className="mt-0 mr-auto mb-0 ml-auto" style={{ maxWidth: 760, display: "flex", flexDirection: "column", gap: 9 }}>
          {composer}
          {actions}
        </div>
      </div>}
    </div>
  );
}
