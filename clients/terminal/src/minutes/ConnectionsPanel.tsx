"use client";
/** Trusted connection UI. MCP requests are metadata; consent starts only on a human click. */
import { useEffect, useRef, useState } from 'react';
import { OAuthConnectionForm } from './OAuthConnectionForm';
import { GitConnection } from './GitConnection';
import { SecretConnectionForm, type ConnectionSetup } from './SecretConnectionForm';
import { connectionStyle as cs } from './connectionStyles';
import { type as ty, T, surface } from './tokens';
type Connection = {id:string; provider:string; label:string; status:string; created:number; setup_request?:string; account?:string; application_configured?:boolean; setup?:ConnectionSetup};
type Focus = {provider:string; label?:string; id?:string};
import { CONNECTIONS_CLOSE, CONNECTIONS_OPEN } from "./connectionEvents";
export { CONNECTIONS_CLOSE, CONNECTIONS_OPEN } from "./connectionEvents";
const supported=(c:Connection)=>['google_email','google_calendar','custom_secret'].includes(c.provider);
async function call(path:string,body?:unknown) {
  const r=await fetch('/api/connections/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  if(!r.ok) throw new Error('Connections unavailable. Please try again.');
  return r.json();
}
export function ConnectionsPanel({onOpenChange,onModeChange}:{onOpenChange?:(open:boolean)=>void;onModeChange?:(mode:'page'|'panel'|null)=>void}={}) {
  const [mode,setMode]=useState<'page'|'panel'>('page');
  const [open,setOpen]=useState(false), [rows,setRows]=useState<Connection[]>([]);
  useEffect(()=>{onOpenChange?.(open);onModeChange?.(open?mode:null);},[open,mode,onOpenChange,onModeChange]);
  const [focus,setFocus]=useState<Focus|null>(null);
  const focusRef=useRef<Focus|null>(null);
  const select=(next:Focus|null)=>{focusRef.current=next;setFocus(next);};
  const [error,setError]=useState(''), [busy,setBusy]=useState(false);
  const [editing,setEditing]=useState<string|null>(null);
  const [deleting,setDeleting]=useState<string|null>(null);
  const [authorizing,setAuthorizing]=useState(false);
  const [labels,setLabels]=useState<Record<string,string>>({});
  useEffect(()=>{
    let stopped=false;
    const result=new URLSearchParams(window.location.search).get('connection_result');
    if(result==='refused')setError('Authorization was not completed. You can try again.');
    const seen=new Map<string,string>();
    let initialized=false;
    const load=async()=>{
      try {
        const data=await call('list');
        if(stopped)return;
        const connections=(data.connections as Connection[]).filter(supported);
        setRows(connections);
        const changed=connections.filter(c=>seen.get(c.id)!==(c.setup_request||c.id));
        for(const c of connections)seen.set(c.id,c.setup_request||c.id);
        // Initial inventory is not a setup request: old pending accounts must not steal focus.
        if(initialized) {
          const requested=changed[0];
          if(requested){setMode('panel');select({provider:requested.provider,label:requested.label,id:requested.id});setOpen(true);}
        }
        initialized=true;
      } catch { /* Background polling stays quiet; opening the panel shows failures. */ }
    };
    const show=(event?:Event)=>{
      const detail=event instanceof CustomEvent?event.detail:undefined;
      const wanted=detail&&['google_email','google_calendar','custom_secret','github'].includes(detail.provider)
        ? {provider:detail.provider,label:typeof detail.label==='string'?detail.label:undefined,id:detail.connection_id}:null;
      select(wanted);setMode(wanted?'panel':'page');setOpen(true);setError('');
      call('list').then(d=>{if(!stopped)setRows(d.connections.filter(supported));}).catch(e=>{if(!stopped)setError(e.message);});
    };
    const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('vexa:connection-consent'):null;
    if(channel)channel.onmessage=(event)=>{
      if(!['connected','refused'].includes(event.data?.status))return;
      setAuthorizing(false);setOpen(true);
      setError(event.data.status==='refused'?'Authorization was not completed. Please try again.':'');
      void load();
    };
    const hide=()=>setOpen(false);
    window.addEventListener(CONNECTIONS_CLOSE,hide);
    window.addEventListener(CONNECTIONS_OPEN,show);
    void load(); const timer=setInterval(()=>{if(!document.hidden)void load();},5000);
    if(new URLSearchParams(window.location.search).has('connection_result')||new URLSearchParams(window.location.search).has('connection'))show();
    return()=>{stopped=true;channel?.close();clearInterval(timer);window.removeEventListener(CONNECTIONS_OPEN,show);window.removeEventListener(CONNECTIONS_CLOSE,hide);};
  },[]);
  async function run(action:()=>Promise<void>) {setBusy(true);setError('');try{await action();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function connect(provider:string) {
    const result=await call('request',{provider,label:labels[provider]||''});
    const data=await call('list');setRows(data.connections.filter(supported));
    if(!result.connection_id)throw new Error('Connection request failed');
    select({provider,id:result.connection_id,label:labels[provider]||undefined});
  }
  async function authorize(c:Connection) {
    const popup=window.open('about:blank','vexa-connection-consent','popup,width=520,height=720');
    if(!popup)throw new Error('Allow popups for Minutes, then try again.');
    try {
      popup.document.title='Connecting your account';
      popup.document.body.textContent='Opening secure authorization…';
      const result=await call(c.id+'/authorize',{});
      const url=new URL(result.authorize_url);
      if(c.setup?.oauth ? url.origin+url.pathname!==c.setup.oauth.authorization_url : url.origin!=='https://accounts.google.com'||url.pathname!=='/o/oauth2/v2/auth')throw new Error('Unexpected authorization destination');
      popup.opener=null;
      popup.location.replace(url.href);
      setAuthorizing(true);
    } catch(e) {popup.close();throw e;}
  }
  const button={...cs.button,cursor:busy?'wait':'pointer',opacity:busy?0.6:1};
  if(!open)return null;
  const selected=focus?rows.find(c=>focus.id?c.id===focus.id:c.provider===focus.provider&&(!focus.label||c.label===focus.label)):undefined;
  const providerNames:Record<string,string>={google_email:'Gmail',google_calendar:'Google Calendar',custom_secret:'Custom secret',github:'Git repositories'};
  const title=focus?'Connect '+(selected?.label||focus.label||providerNames[focus.provider]):'Connections';
  return <section role={mode==='page'?'region':'dialog'} aria-label="Connections" data-connections-surface={mode} data-connections-panel style={{...ty.body,gridColumn:mode==='page'?'2 / 4':3,gridRow:'1 / span 2',minWidth:0,minHeight:0,display:'flex',flexDirection:'column',background:mode==='page'?surface.pages:surface.center,color:'var(--t1)',borderLeft:'1px solid var(--line)',lineHeight:1.5}}>
    <header style={{display:'flex',alignItems:'center',justifyContent:'space-between',height:T.headerH,boxSizing:'border-box',flex:'none',padding:'0 16px',borderBottom:'1px solid var(--line)'}}>
      <h2 style={{...ty.title,margin:0}}>{title}</h2>
      {mode==='page'&&focus&&<button style={{...button,marginLeft:'auto',marginRight:8}} onClick={()=>select(null)}>All connections</button>}
      <button aria-label="Close connections" onClick={()=>setOpen(false)} style={{...button,padding:'4px 10px',background:'transparent'}}>×</button>
    </header>
    <div style={{flex:1,minHeight:0,overflowY:"auto",padding:mode==='page'?32:20}}>
    <div style={{maxWidth:960,margin:0}}>
    <p style={{...ty.lens,margin:'0 0 8px'}}>{mode==='page'?'Your accounts & services':'Secure setup'}</p>
    <p style={{...ty.body,color:'var(--t2)',margin:'0 0 24px',maxWidth:640}}>{mode==='page'?'Manage the accounts your agents can use.':'Complete this connection to continue with your agent.'} Credentials stay private in the secure store.</p>
    {error&&<p role="alert" style={{padding:12,border:'1px solid #c65d46',borderRadius:8,color:'var(--t1)'}}>{error}</p>}
    {authorizing&&<p role="status" style={{...ty.body,padding:12,background:'var(--panel)',borderRadius:8}}>Finish authorization in the provider window. This panel updates when you return.</p>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0, 1fr)',gap:0,alignItems:'start'}}>
    {(!focus||focus.provider==='github')&&<GitConnection />}
    {(['google_email','google_calendar','custom_secret'] as const).filter(provider=>!focus||provider===focus.provider).map(provider=>{
      const connections=focus?(selected?[selected]:[]):rows.filter(c=>c.provider===provider);
      const label=(provider==='custom_secret'&&focus?(selected?.label||focus.label):undefined)||({google_email:'Gmail',google_calendar:'Google Calendar',custom_secret:'Custom secret'}[provider]);
      return <section key={provider} style={cs.card}>
        <h3 style={{...ty.title,margin:'0 0 6px'}}>{label}</h3>
        <p style={{...ty.body,color:'var(--t2)',margin:'0 0 16px'}}>{provider==='custom_secret'?'Store any secret privately. Optionally configure an HTTPS API where the agent can use it.':provider==='google_email'?'Read email and save drafts. Minutes does not send mail.':'Read calendar events.'}</p>

        {connections.map(c=><div key={c.id} style={{borderTop:'1px solid var(--line)',padding:'12px 0'}}>
          <div style={{display:'flex',alignItems:'flex-start',gap:16}}>
          <details open={focus?true:undefined} style={{flex:1,minWidth:0}}>
          <summary style={{cursor:'pointer',...ty.bodyStrong,marginBottom:12}}>{c.label}{c.account&&<span style={{...ty.meta,display:'block',marginTop:4}}>{c.account}</span>}
          <span data-connection-status={c.status} style={{...ty.meta,display:'block',marginTop:6,color:c.status==='ready'&&!(c.setup?.oauth&&!c.application_configured)?'var(--green)':'var(--t3)'}}>{c.setup?.oauth&&!c.application_configured?'Setup changes need approval':c.status==='ready'?'● Connected':c.status==='disconnected'?'Disconnected':'Ready to connect · your consent is required'}</span></summary>
          {c.setup?.oauth?<OAuthConnectionForm key={c.id+String(c.setup_request)} setup={c.setup} configured={!!c.application_configured} ready={c.status==='ready'} busy={busy} onAuthorize={()=>void run(()=>authorize(c))} onSave={async body=>{setBusy(true);setError('');try{await call(c.id+'/oauth-application',{...body,setup_request:c.setup_request});const d=await call('list');setRows(d.connections.filter(supported));}catch(e){setError((e as Error).message);throw e;}finally{setBusy(false);}}}/>:provider==='custom_secret'&&c.status==='ready'&&editing!==c.id?<button style={button} onClick={()=>setEditing(c.id)}>Edit connection</button>:provider==='custom_secret'?<SecretConnectionForm key={c.id+String(c.setup_request)} setup={c.setup} hasCredential={c.status==='ready'} busy={busy} onSave={async body=>{await run(async()=>{await call(c.id+'/custom-secret',{...body,setup_request:c.setup_request||''});setEditing(null);const d=await call('list');setRows(d.connections.filter(supported));});}}/>:<button disabled={busy} style={{...button,...cs.primary,width:mode==='panel'?'100%':undefined}} onClick={()=>void run(()=>authorize(c))}>{c.status==='ready'?(c.provider==='google_email'?'Enable drafts / reconnect':'Reconnect with Google'):'Continue with Google'}</button>}
          {c.status==='ready'&&<button disabled={busy} style={{...button,marginTop:8,marginLeft:mode==='page'?8:0,width:mode==='panel'?'100%':undefined}} onClick={()=>void run(async()=>{await call(c.id+'/disconnect',{});const d=await call('list');setRows(d.connections.filter(supported));})}>Disconnect</button>}
          </details>
          <button aria-label={'Delete '+c.label+' connection'} disabled={busy} style={{...button,background:'transparent',color:'var(--danger)',padding:'5px 9px'}} onClick={()=>setDeleting(c.id)}>Delete</button>
          </div>
          {deleting===c.id&&<div role="alert" style={{...ty.body,marginTop:12,padding:12,background:surface.raised}}>
            Remove this connection and stop agent access? Audit history is retained.
            <div style={{display:'flex',gap:8,marginTop:8}}><button disabled={busy} style={{...button,color:'var(--danger)'}} onClick={()=>void run(async()=>{await call(c.id+'/delete',{});setRows(prev=>prev.filter(row=>row.id!==c.id));setDeleting(null);if(focus){select(null);if(mode==='panel')setOpen(false);}})}>Delete connection</button><button disabled={busy} style={button} onClick={()=>setDeleting(null)}>Cancel</button></div>
          </div>}
        </div>)}
        {focus&&!selected&&<p role="status">Preparing connection…</p>}
        {!focus&&<>
        <label style={{...ty.meta,display:'block',marginBottom:6}}>Connection label</label>
        <input aria-label={label+' account label'} placeholder="e.g. Personal or Work" value={labels[provider]||''} onChange={e=>setLabels({...labels,[provider]:e.target.value})} maxLength={80} style={{...ty.body,boxSizing:'border-box',width:mode==='page'?280:'100%',maxWidth:'100%',padding:10,border:'1px solid var(--line2)',borderRadius:8,background:'var(--sidebar)',color:'var(--t1)',marginBottom:8}}/>
        <button disabled={busy} style={{...button,display:'block'}} onClick={()=>void run(()=>connect(provider))}>{connections.length?'Add another '+label+' account':'Connect '+label}</button>
        </>}
      </section>;
    })}
    </div>
    {(!focus||['google_email','google_calendar'].includes(focus.provider))&&<p style={{...ty.meta,lineHeight:1.6}}>Google’s draft permission also permits sending at the provider level; Minutes exposes draft creation only. Google authorization opens in a secure popup. Your Minutes workspace stays open.</p>}
    </div></div>
  </section>;
}
