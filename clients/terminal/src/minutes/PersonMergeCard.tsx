"use client";
/** PersonMergeCard — "Are these accounts all yours?" (PRD 0.13.4 D35, S3).
 *
 *  One human may sign in with several addresses. Until they say so, each login is linked to the same
 *  Person only because the organisation's records list both addresses: grants reach every one of
 *  them, and this card asks the person to confirm. It is the ONLY way two accounts merge — never
 *  automatic, never an agent, never an administrator. Each login confirms from its own session (a
 *  sign-in is the only proof that an address is yours), and the person chooses the primary address:
 *  where invites and reminders go. "Not me" splits the accounts again.
 *
 *  Renders nothing when there is no card for this person, or when the CRM is not part of this
 *  deployment. Addresses other than the one signed in are shown masked. */
import { useCallback, useEffect, useState } from "react";
import { confirmMerge, declineMerge, mergeSentence, openWorkspacesToEveryLogin, readMe, type MergeCard } from "../surfaces/personApi";

export function PersonMergeCard({ load = readMe }: { load?: typeof readMe }) {
  const [card, setCard] = useState<MergeCard | null>(null);
  const [primary, setPrimary] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    load().then((me) => {
      setCard(me.merge);
      const mine = me.merge?.addresses.find((a) => a.yours)?.option ?? "";
      setPrimary((p) => p || me.merge?.primary || mine);
    }).catch(() => setCard(null));
  }, [load]);
  useEffect(() => { refresh(); }, [refresh]);

  if (!card && !said) return null;
  if (!card) return <div className="vx-person-merge" data-person-merge="done" role="status">{said}</div>;

  const act = async (fn: () => Promise<{ status: string }>) => {
    setBusy(true); setError(null);
    try {
      const r = await fn();
      if (r.status === "merged") { await openWorkspacesToEveryLogin(); setSaid("Done: these accounts are one person now. Either address opens everything that is yours."); setCard(null); }
      else if (r.status === "declined") { setSaid("Understood: those accounts stay separate."); setCard(null); }
      else refresh();
    } catch (e) {
      setError((e as Error).message || "That did not go through");
    } finally {
      setBusy(false);
    }
  };

  const waiting = card.confirmed_by_you && card.waiting_on > 0;
  return (
    <div className="vx-person-merge" data-person-merge={card.id} role="group" aria-label="Your accounts">
      <div className="vx-person-merge-says">{mergeSentence(card)}</div>
      {!waiting && (
        <fieldset className="vx-person-merge-options">
          <legend className="vx-person-merge-legend">Primary address: invites and reminders go here</legend>
          {card.addresses.map((a) => (
            <label key={a.option} className="vx-person-merge-option">
              <input type="radio" name={`primary-${card.id}`} value={a.option} checked={primary === a.option}
                onChange={() => setPrimary(a.option)} />
              <span>{a.address}{a.yours ? " (signed in now)" : ""}</span>
            </label>
          ))}
        </fieldset>
      )}
      {!waiting && (
        <div className="vx-person-merge-acts">
          <button type="button" className="vx-kh-btn vx-kh-btn-primary" disabled={busy} data-person-merge-confirm=""
            onClick={() => { void act(() => confirmMerge(card.id, primary || undefined)); }}>Yes, they are all mine</button>
          <button type="button" className="vx-kh-btn" disabled={busy} data-person-merge-decline=""
            onClick={() => { void act(() => declineMerge(card.id)); }}>Not me</button>
        </div>
      )}
      {error && <div className="c-danger" role="alert">{error}</div>}
    </div>
  );
}
