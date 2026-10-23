"use client";
import { useEffect, useState } from 'react';
import { getGitToken, setGitToken } from '../data/workspaceApi';
import { BUILTIN_GIT_PROVIDERS, listGitProviders, setKnownGitProviders, type GitProvider, type GitProviderList } from '../data/gitProviders';
import { connectionStyle as cs } from './connectionStyles';
import { AttachRepo } from './AttachRepo';
import { type as ty } from './tokens';

/** What a token needs on each kind of provider — the least that publish, push and pull work with. */
function scopeHint(p: GitProvider): string {
  if (p.kind === 'gitlab') return 'Scopes: api + write_repository, with the Developer role or above in the group (Maintainer if the group restricts project creation).';
  if (p.kind === 'github') return 'Scopes: repo, or a fine-grained token with Contents read-write (and Administration to create repositories).';
  return 'A token or password with read-write access to your repositories.';
}

interface TokenState { saved: boolean | null; masked: string | null }

/** Secrets are submitted directly to the authenticated API, never to a chat. One saved token per
 *  configured git provider; each is sent only to that provider's host. */
export function GitConnection() {
  const [list,setList]=useState<GitProviderList>(BUILTIN_GIT_PROVIDERS);
  const [states,setStates]=useState<Record<string,TokenState>>({});
  const [importing,setImporting]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{
    let live=true;
    listGitProviders().then(l=>{
      if(!live)return;
      setKnownGitProviders(l);setList(l);
      // A provider-aware server says per provider; an older one only knows the default token.
      const next:Record<string,TokenState>={};
      for(const p of l.providers)if(typeof p.token_saved==='boolean')next[p.name]={saved:p.token_saved,masked:p.masked??null};
      setStates(s=>({...next,...s}));
    }).catch(()=>{});
    getGitToken().then(t=>{if(live)setStates(s=>({...s,[t.provider||'']:{saved:t.set,masked:t.masked??null}}));})
      .catch(()=>{if(live)setError('Could not read Git connection status.');});
    return()=>{live=false;};
  },[]);
  const stateOf=(p:GitProvider):TokenState=>states[p.name]??(p.name===list.default?states['']:undefined)??{saved:null,masked:null};
  async function save(p:GitProvider,token:string|null) {
    setError('');
    // The default provider is addressed without a name, which every server version understands.
    const t=p.name===list.default?await setGitToken(token):await setGitToken(token,p.name);
    setStates(s=>({...s,[p.name]:{saved:t.set,masked:t.masked??null}}));
  }
  return <section aria-label="Git connection" style={cs.card}>
    <h3 style={{...ty.title,margin:'0 0 8px'}}>Git repositories</h3>
    <p style={{...ty.body,color:'var(--t2)',margin:'0 0 16px'}}>Save a token for each git provider you use over HTTPS. Choose only the repositories and permissions you need. Tokens are kept in the credential broker and sent only to that provider's host.</p>
    {list.providers.map(p=><ProviderToken key={p.name} provider={p} state={stateOf(p)} onSave={(t)=>save(p,t)} onError={setError}/>)}
    <div style={{marginTop:16}}><button style={cs.button} onClick={()=>setImporting(v=>!v)}>Attach repository as a workspace</button></div>
    {importing&&<AttachRepo embedded onClose={()=>setImporting(false)}/>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}

function ProviderToken({provider:p,state,onSave,onError}:{provider:GitProvider;state:TokenState;onSave:(token:string|null)=>Promise<void>;onError:(m:string)=>void}) {
  const [value,setValue]=useState('');
  const [busy,setBusy]=useState(false);
  const id=`git-connection-token-${p.name}`;
  async function save(token:string|null) {
    setBusy(true);setValue('');
    try{await onSave(token);}catch{onError(`Could not save the ${p.label} token. Please try again.`);}finally{setBusy(false);}
  }
  return <div role="group" aria-label={`${p.label} (${p.host})`} className="mb-3">
    <p style={ty.meta}><strong>{p.label}</strong> · {p.host}</p>
    <p role="status" style={{...ty.meta,color:state.saved?'var(--green)':'var(--t3)'}}>{state.saved===null?'Checking credentials…':state.saved?(state.masked?`Token saved securely (${state.masked})`:'Token saved securely'):'No saved token'}</p>
    <label style={ty.meta} htmlFor={id}>{p.label} token</label>
    <input id={id} type="password" autoComplete="off" value={value} onChange={e=>setValue(e.target.value)} disabled={busy} style={cs.input}/>
    <p style={ty.meta} className="c-3">{scopeHint(p)}</p>
    <button style={{...cs.button,...cs.primary}} disabled={busy||!value.trim()} onClick={()=>void save(value.trim())}>Save securely</button>
    {state.saved&&<button style={{...cs.button,marginLeft:8}} disabled={busy} onClick={()=>void save(null)}>Remove token</button>}
  </div>;
}
