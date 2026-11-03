"use client";
/** The Call button beside a chat's people (ADR-0048 decision 6), and the owner's review after a call
 *  (decision 7).
 *
 *  Shown only where the deployment has calls (`GET /api/sessions/{s}/call` says `calls: true`). It
 *  reads "Call" when the session has none and "Join call · N" when one is live; either way a click
 *  joins (starting the call first if there is none) and the dock opens on the notice that the call is
 *  transcribed; the room connects, and the microphone opens, only after the participant presses Join.
 *
 *  REVIEW: after a call the post-meeting run proposes changes — pages, entity fields, commitments.
 *  The meeting's owner sees "Review · N" here and accepts (as is or edited) or rejects each one; until
 *  they accept, nothing lands in a shared workspace. Everyone else sees the list, read-only. */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../data/apiClient";
import { useFeature } from "../platform/features";
import { decideProposal, getCall, listProposals, type CallState, type Proposal } from "../surfaces/callsApi";
import { enterCall, useActiveCall } from "./callState";
import "./calls.css";

const btn: React.CSSProperties = { flex: "none" };

/** Hidden while `calls` is switched off (`platform/features`). */
export function CallButton(p: { session: string; label: string }) {
  return useFeature("calls") ? <CallButtonBody {...p} /> : null;
}

function CallButtonBody({ session, label }: { session: string; label: string }) {
  const [state, setState] = useState<CallState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [review, setReview] = useState<{ you?: string; proposals: Proposal[] } | null>(null);
  const [open, setOpen] = useState(false);
  const { current } = useActiveCall();
  const here = current?.session === session;

  const refresh = useCallback(() => {
    getCall(session).then(setState).catch((e) => {
      // A thread the store has not seen yet (404) or no store (503): no Call button, quietly.
      if (e instanceof ApiError && (e.status === 404 || e.status === 503)) setState(null);
    });
    listProposals(session).then(setReview).catch(() => setReview(null));
  }, [session]);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [refresh, here]);

  const pending = (review?.proposals ?? []).filter((p) => p.status === "open");
  const mine = pending.filter((p) => p.owner === review?.you);

  async function join() {
    setBusy(true); setErr("");
    try { await enterCall(session, label); refresh(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not join the call."); }
    finally { setBusy(false); }
  }

  if (!state?.calls && !pending.length) return null;
  const live = state?.call;
  return (
    <span style={{ position: "relative", display: "inline-flex", gap: 6, flex: "none" }}>
      {state?.calls && !here && (
        <button data-call="join" className="vx-callbtn" data-live={live ? "" : undefined} style={btn} disabled={busy}
          title={live?.transcribed === false ? "Join the call" : "Join the call. Calls are transcribed; everyone in it sees this notice."}
          onClick={join}>
          {busy ? "Joining…" : live ? `Join call · ${live.participants}` : "Call"}
        </button>
      )}
      {here && <span className="vx-call-chip vx-call-in" data-call="in">In call</span>}
      {pending.length > 0 && (
        <button data-call="review" className="vx-callbtn" style={btn} onClick={() => setOpen((v) => !v)}
          title={mine.length ? "Changes proposed after the call wait for your decision" : "Changes proposed after the call"}>
          Review · {pending.length}
        </button>
      )}
      {err && <span role="alert" className="vx-call-meta vx-call-alert">{err}</span>}
      {open && review && (
        <ReviewPanel session={session} you={review.you} proposals={pending}
          onDone={() => { refresh(); }} onClose={() => setOpen(false)} />
      )}
    </span>
  );
}

function ReviewPanel(p: { session: string; you?: string; proposals: Proposal[]; onDone: () => void; onClose: () => void }) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  async function decide(pr: Proposal, accept: boolean) {
    setNote("");
    try {
      const edited = edits[pr.id];
      await decideProposal(p.session, pr.id, accept, accept && edited !== undefined ? edited : undefined);
      p.onDone();
    } catch (e) { setNote(e instanceof Error ? e.message : "That did not go through."); }
  }
  return (
    <div role="dialog" aria-label="Review proposed changes" data-call="review-panel"
      className="vx-call-review"
      style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, width: 420, maxHeight: 520, overflow: "auto" }}>
      <div className="vx-call-meta vx-call-review-head">
        Proposed after the call. Nothing lands in a workspace until the meeting&apos;s owner accepts it.
        <button onClick={p.onClose} aria-label="Close" className="vx-callbtn" data-close="" style={btn}>×</button>
      </div>
      {p.proposals.map((pr) => {
        const owner = pr.owner === p.you;
        return (
          <div key={pr.id} data-proposal={pr.id} className="vx-call-proposal">
            <div className="vx-call-chip vx-call-proposal-kind">
              {pr.kind}{pr.workspace ? ` · ${pr.workspace}/${pr.path}` : ""} {pr.title ? `— ${pr.title}` : ""}
            </div>
            <textarea aria-label={`Proposal ${pr.title || pr.id}`} readOnly={!owner}
              value={edits[pr.id] ?? pr.body} onChange={(e) => setEdits((m) => ({ ...m, [pr.id]: e.target.value }))}
              className="vx-call-proposal-body" style={{ width: "100%", minHeight: 70 }} />
            {owner ? (
              <div className="vx-call-proposal-acts" style={{ display: "flex", gap: 6 }}>
                <button className="vx-callbtn" style={btn} onClick={() => decide(pr, true)}>{edits[pr.id] !== undefined ? "Accept edited" : "Accept"}</button>
                <button className="vx-callbtn" style={btn} onClick={() => decide(pr, false)}>Reject</button>
              </div>
            ) : <div className="vx-call-meta">Waiting for the meeting&apos;s owner.</div>}
          </div>
        );
      })}
      {note && <div role="alert" className="vx-call-meta vx-call-alert">{note}</div>}
    </div>
  );
}
