"use client";
import { useState } from "react";

/** Edit the visible title in place. Failed saves keep the input and explain the failure. */
export function ChatName({ label, onRename }: { label: string; onRename?: (name: string) => Promise<void> }) {
  const [editing, edit] = useState(false);
  const [value, setValue] = useState(label);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!editing) return <button title="Rename chat" aria-label={`Rename ${label}`} disabled={!onRename}
    onClick={() => { setValue(label); setError(""); edit(true); }}
    className="c-inherit bg-none bd-none p-0" style={{ font:"inherit", cursor:"text", textAlign:"left", minWidth:0, maxWidth:"100%", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{label}</button>;
  return <form style={{display:"flex",flex:1,minWidth:0,gap:6,alignItems:"center"}} onSubmit={async e => {
    e.preventDefault(); if (!value.trim() || busy) return;
    setBusy(true); setError("");
    try { await onRename?.(value.trim()); edit(false); }
    catch { setError("Couldn't save. Try again."); }
    finally { setBusy(false); }
  }}>
    <input aria-label="Chat name" autoFocus maxLength={100} value={value} disabled={busy}
      onFocus={e => e.target.select()} onChange={e=>setValue(e.target.value)}
      onKeyDown={e=>{if(e.key==="Escape" && !busy){e.preventDefault();edit(false);}}}
      className="c-1 bg-0 bd r-sm" style={{ font:"inherit", width:120, flex:1, minWidth:50 }} />
    <button className="t-xs c-accent bg-none bd-none" style={{ font:"inherit", cursor:"pointer", flex:"none" }} type="submit" disabled={busy || !value.trim()}>{busy ? "Saving…" : "Save"}</button>
    <button className="t-xs c-2 bg-none bd-none" style={{ font:"inherit", cursor:"pointer", flex:"none" }} type="button" disabled={busy} onClick={()=>edit(false)}>Cancel</button>
    {error && <span role="alert" className="t-xs">{error}</span>}
  </form>;
}
