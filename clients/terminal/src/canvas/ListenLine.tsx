"use client";
/** "LISTENING FOR: …" — the meeting's listening brief, on the meeting page (ADR transcript signals:
 *  "the brief sits on the meeting page, so people in the meeting can see what Vexa is listening for").
 *
 *  One quiet line: the focus, then the labels that wake Vexa. A click opens a plain editor — the focus,
 *  and each label with its question and whether it wakes Vexa — and Save writes the page through the
 *  same file write every page uses (`listenBrief.writeListenBrief` keeps the rest of the page as it
 *  was). "To Vexa" is always there: it can be read, never removed. Hidden when signals are switched
 *  off or the page has no brief; a read-only page shows the line without the editor. */
import "./listenLine.css";
import { useState } from "react";
import { Radio, X } from "lucide-react";
import { useFeature } from "../platform/features";
import { readListenBrief, writeListenBrief } from "./listenBrief";
import { ADDRESSED, labelText, wakeLabels, type ListenBrief, type ListenLabel } from "./signalMarks";

export const SIGNALS_FEATURE = "transcript_signals";

export function ListenLine({ raw, readonly = false, onSave }: { raw: string; readonly?: boolean; onSave: (next: string) => Promise<void> | void }) {
  const on = useFeature(SIGNALS_FEATURE);
  const brief = readListenBrief(raw);
  const [draft, setDraft] = useState<ListenBrief | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  if (!on || !brief) return null;
  const wakes = wakeLabels(brief);
  const open = () => { if (!readonly) { setDraft({ ...brief, labels: brief.labels.map((l) => ({ ...l })) }); setError(""); } };

  const line = (
    <button type="button" data-listen-line="" className="vx-listen-line" onClick={open} aria-expanded={!!draft}
      title={readonly ? "What Vexa listens for in this meeting" : "What Vexa listens for in this meeting — click to change it"}
      disabled={!!draft}>
      <Radio size={13} strokeWidth={1.75} aria-hidden className="vx-listen-icon" />
      <span className="vx-listen-lead">Listening for</span>
      {brief.focus ? <span className="vx-listen-focus">{brief.focus}</span> : null}
      <span className="vx-listen-labels">
        {wakes.map((n) => <span key={n} data-listen-label={n} className="vx-listen-label">{labelText(n)}</span>)}
      </span>
    </button>
  );
  if (!draft) return <div className="vx-listen">{line}</div>;

  const setLabel = (i: number, patch: Partial<ListenLabel>) =>
    setDraft({ ...draft, labels: draft.labels.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const save = async () => {
    setSaving(true); setError("");
    try { await onSave(writeListenBrief(raw, draft)); setDraft(null); }
    catch { setError("Could not save. Retry."); }
    finally { setSaving(false); }
  };
  return (
    <div className="vx-listen">
      {line}
      <div data-listen-editor="" role="group" aria-label="What Vexa listens for" className="vx-listen-editor">
        <label className="vx-listen-field">
          <span className="vx-listen-field-name">What to listen for</span>
          <textarea data-listen-focus="" rows={2} value={draft.focus ?? ""} className="vx-listen-input"
            onChange={(e) => setDraft({ ...draft, focus: e.target.value })} />
        </label>
        <div className="vx-listen-field-name" id="vx-listen-labels-h">Labels</div>
        <ul className="vx-listen-rows" aria-labelledby="vx-listen-labels-h">
          {draft.labels.map((l, i) => {
            const fixed = l.name === ADDRESSED;
            return <li key={i} data-listen-row={l.name} className="vx-listen-row">
              {fixed
                ? <span className="vx-listen-fixed">{labelText(l.name)}</span>
                : <input aria-label="Label" value={l.name} className="vx-listen-input vx-listen-name" onChange={(e) => setLabel(i, { name: e.target.value })} />}
              <input aria-label={`Question for ${labelText(l.name) || "this label"}`} placeholder="What it means, as a question"
                value={fixed ? (l.question ?? "Is someone talking to Vexa?") : (l.question ?? "")} disabled={fixed}
                className="vx-listen-input vx-listen-question" onChange={(e) => setLabel(i, { question: e.target.value })} />
              <label className="vx-listen-wake">
                <input type="checkbox" checked={fixed || !!l.wake} disabled={fixed} onChange={(e) => setLabel(i, { wake: e.target.checked })} />
                Wakes Vexa
              </label>
              {!fixed && <button type="button" data-listen-remove="" className="vx-listen-icon-btn" aria-label={`Remove ${labelText(l.name) || "label"}`}
                onClick={() => setDraft({ ...draft, labels: draft.labels.filter((_, j) => j !== i) })}><X size={13} aria-hidden /></button>}
            </li>;
          })}
        </ul>
        <div className="vx-listen-acts">
          <button type="button" data-listen-add="" className="vx-listen-btn"
            onClick={() => setDraft({ ...draft, labels: [...draft.labels, { name: "", wake: false }] })}>Add label</button>
          <span className="vx-listen-spacer" />
          {error && <span role="alert" className="vx-listen-error">{error}</span>}
          <button type="button" data-listen-cancel="" className="vx-listen-btn" disabled={saving} onClick={() => setDraft(null)}>Cancel</button>
          <button type="button" data-listen-save="" className="vx-listen-btn vx-listen-btn-primary" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
