"use client";
/** A notice's acts, inline (#1783): the one that comes first (Accept, Join, Open transcript, Open
 *  chat, Open) and, for an invite or a call, Decline. Once answered, the notice says what was done
 *  ("Joined" · Open, or "Declined"). One tap does it and marks the notice read. */
import { useState } from "react";
import { actOn, primaryAction, secondaryAction, stateLabel, type Notice } from "./notifications";

export function NoticeActs({ n, onDone }: { n: Notice; onDone?: () => void }) {
  const [busy, setBusy] = useState(false);
  const first = primaryAction(n), second = secondaryAction(n), done = stateLabel(n);
  if (!first && !second && !done) return null;
  const run = async (action: NonNullable<ReturnType<typeof primaryAction>>["action"]) => {
    setBusy(true);
    try { await actOn(n, action); } finally { setBusy(false); }
    onDone?.();
  };
  return (
    <div className="vx-notice-acts" data-notice-acts="">
      {done && <span className="vx-notice-state" data-notice-state={n.state}>{done}</span>}
      {first && <button type="button" className="vx-notice-act" data-primary="" data-notice-act={first.action}
        disabled={busy} onClick={() => void run(first.action)}>{first.label}</button>}
      {second && <button type="button" className="vx-notice-act" data-notice-act={second.action}
        disabled={busy} onClick={() => void run(second.action)}>{second.label}</button>}
    </div>
  );
}
