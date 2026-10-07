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
    style={{font:"inherit",color:"inherit",background:"none",border:0,padding:0,cursor:"text",textAlign:"left",minWidth:0,maxWidth:"100%",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{label}</button>;
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
      style={{font:"inherit",color:"var(--t1)",background:"var(--bg)",border:"1px solid var(--line)",borderRadius:4,width:120,flex:1,minWidth:50}} />
    <button style={{font:"inherit",fontSize:12,color:"var(--accent)",background:"none",border:0,cursor:"pointer",flex:"none"}} type="submit" disabled={busy || !value.trim()}>{busy ? "Saving…" : "Save"}</button>
    <button style={{font:"inherit",fontSize:12,color:"var(--t2)",background:"none",border:0,cursor:"pointer",flex:"none"}} type="button" disabled={busy} onClick={()=>edit(false)}>Cancel</button>
    {error && <span role="alert" style={{fontSize:12}}>{error}</span>}
  </form>;
}
