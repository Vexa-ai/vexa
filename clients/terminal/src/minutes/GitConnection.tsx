"use client";
import { useEffect, useState } from 'react';
import { getGitToken, setGitToken } from '../surfaces/workspaceApi';
import { connectionStyle as cs } from './connectionStyles';
import { AttachRepo } from './AttachRepo';
import { type as ty } from './tokens';

/** Secrets are submitted directly to the authenticated API, never to a chat. */
export function GitConnection() {
  const [saved,setSaved]=useState<boolean|null>(null),[value,setValue]=useState('');
  const [importing,setImporting]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{let live=true;getGitToken().then(s=>{if(live)setSaved(s.set);}).catch(()=>{if(live)setError('Could not read Git connection status.');});return()=>{live=false;};},[]);
  async function save(token:string|null) {
    setBusy(true);setError('');setValue('');
    try{setSaved((await setGitToken(token)).set);}catch{setError('Could not save Git credentials. Please try again.');}finally{setBusy(false);}
  }
  const button=cs.button;
  return <section aria-label="Git connection" style={cs.card}>
    <h3 style={{...ty.title,margin:'0 0 8px'}}>Git repositories</h3>
    <p style={{...ty.body,color:'var(--t2)',margin:'0 0 16px'}}>Save a GitHub token for HTTPS repositories. Choose only the repositories and permissions you need. Credentials are stored securely and reused for your repositories.</p>
    <p role="status" style={{...ty.meta,color:saved?'var(--green)':'var(--t3)'}}>{saved===null?'Checking credentials…':saved?'Token saved securely':'No saved token'}</p>
    <label style={ty.meta} htmlFor="git-connection-token">GitHub token</label>
    <input id="git-connection-token" type="password" autoComplete="off" value={value} onChange={e=>setValue(e.target.value)} disabled={busy} style={{...ty.body,width:'100%',boxSizing:'border-box',padding:10,margin:'6px 0 10px',background:'var(--sidebar)',color:'var(--t1)',border:'1px solid var(--line2)',borderRadius:8}}/>
    <button style={{...button,...cs.primary}} disabled={busy||!value.trim()} onClick={()=>void save(value.trim())}>Save securely</button>
    {saved&&<button style={{...button,marginLeft:8}} disabled={busy} onClick={()=>void save(null)}>Remove token</button>}
    <div style={{marginTop:16}}><button style={button} onClick={()=>setImporting(v=>!v)}>Attach repository as a workspace</button></div>
    {importing&&<AttachRepo embedded onClose={()=>setImporting(false)}/>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
