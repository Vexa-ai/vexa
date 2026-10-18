"use client";
/** A CALL'S NAME, EDITABLE IN PLACE — the chat title's interaction (`./ChatName`), for a meeting.
 *
 *  Click the title (or press F2 on a Calls row) → an input holding the current name, selected.
 *  Enter saves, Escape cancels. A refused save keeps the input and says why. An empty name clears
 *  the title, so the call goes back to its default label (shown as the placeholder).
 *
 *  The write is `./callRename` — meeting-api's annotate route, owner-only. A person the call is
 *  shared with sees the name as plain text and no control at all. */
import type { CSSProperties } from "react";
import { useRef, useState } from "react";
import type { MeetingMock } from "../surfaces/meetingModel";
import { CALL_TITLE_MAX, CallRenameError, canRenameCall, renameCall } from "./callRename";
import { callLabel } from "./callsList";
import "./minutes.css";

type Named = Pick<MeetingMock, "id" | "shared" | "title_custom" | "platform" | "native_id">;

/** The name with its custom title removed — what an empty save goes back to. */
const defaultLabel = (m: Named) => callLabel({ ...m, title_custom: undefined });

const inputS: CSSProperties = { width: 120, flex: 1, minWidth: 50 };

/** The editor alone. `compact` is the Calls row's form: no Save/Cancel buttons, and leaving the
 *  field saves a change (as a file tree's rename does); the header's form has the chat title's
 *  Save/Cancel buttons. */
export function CallNameEditor(p: { meeting: Named; label: string; compact?: boolean; onDone: () => void }) {
  const [value, setValue] = useState(p.label);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // once the editor has closed (saved or cancelled), a late blur must not save again
  const closed = useRef(false);
  const done = () => { closed.current = true; p.onDone(); };
  const save = async () => {
    if (busy || closed.current) return;
    const next = value.trim();
    // Nothing changed — the shown name, or an empty field on a call that has no title of its own.
    if (next === p.label.trim() || (!next && !p.meeting.title_custom?.trim())) { done(); return; }
    setBusy(true); setError("");
    try { await renameCall(p.meeting.id, next); done(); }
    catch (e) { setError(e instanceof CallRenameError ? e.message : "Couldn't save. Try again."); }
    finally { setBusy(false); }
  };
  return (
    <form data-call-rename={p.meeting.id} style={{ display: "flex", flex: 1, minWidth: 0, gap: 6, alignItems: "center", flexWrap: p.compact ? "wrap" : undefined }}
      onSubmit={(e) => { e.preventDefault(); void save(); }}
      onClick={(e) => e.stopPropagation()}>
      <input aria-label="Call name" autoFocus maxLength={CALL_TITLE_MAX} value={value} disabled={busy}
        placeholder={defaultLabel(p.meeting)}
        onFocus={(e) => e.target.select()} onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation();   // the navigator's own keys (Esc closes it, / filters) stand down while typing
          if (e.key === "Escape" && !busy) { e.preventDefault(); done(); }
        }}
        onBlur={p.compact ? () => { if (!error) void save(); } : undefined}
        className="vx-callname-input" data-compact={p.compact ? "" : undefined}
        style={{ ...inputS, ...(p.compact ? { width: "100%" } : null) }} />
      {!p.compact && <>
        <button className="vx-callname-act" data-tone="accent" style={{ flex: "none" }} type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button className="vx-callname-act" style={{ flex: "none" }} type="button" disabled={busy} onClick={done}>Cancel</button>
      </>}
      {error && <span role="alert" className="vx-callname-error">{error}</span>}
    </form>
  );
}

/** The meeting header's title: a button that opens the editor for the owner, plain text for
 *  everyone else. `style` is the title's own type, kept on both. */
export function CallName(p: { meeting: Named | undefined; label: string; style?: CSSProperties }) {
  const [editing, setEditing] = useState(false);
  const base: CSSProperties = { minWidth: 0, maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", ...p.style };
  if (!p.meeting || !canRenameCall(p.meeting)) return <span data-call-name style={base}>{p.label}</span>;
  if (editing) return <CallNameEditor meeting={p.meeting} label={p.label} onDone={() => setEditing(false)} />;
  return (
    <button type="button" data-call-name title="Rename call" aria-label={`Rename ${p.label}`}
      onClick={() => setEditing(true)}
      className="vx-callname-btn" style={base}>
      {p.label}
    </button>
  );
}
