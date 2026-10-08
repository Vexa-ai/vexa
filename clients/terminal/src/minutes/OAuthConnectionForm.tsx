"use client";
import {useState} from 'react';
import {connectionStyle as cs} from './connectionStyles';
import type {ConnectionSetup} from './SecretConnectionForm';
import { DestinationHost, hostConfirmed } from './DestinationHost';
import { hostIsRecognised, hostOf } from './connectionHosts';
export function OAuthConnectionForm({setup,configured,ready,busy,onSave,onAuthorize,approvedHost}:{setup:ConnectionSetup;configured:boolean;ready:boolean;busy:boolean;onSave:(body:{client_id:string;client_secret:string;confirmed_host?:string})=>Promise<void>;onAuthorize:()=>void;approvedHost?:string}) {
 const [editing,setEditing]=useState(!configured),[clientId,setClientId]=useState(''),[secret,setSecret]=useState('');
 const [confirmed,setConfirmed]=useState('');
 const redirect=typeof window==='undefined'?'':window.location.origin+'/api/auth/callback/google';
 // The client secret goes to the token endpoint; that host is the one to read and confirm (M3).
 const tokenHost=hostOf(setup.oauth?.token_url);
 const others=[['Sign-in page',hostOf(setup.oauth?.authorization_url)],['Agent requests',hostOf(setup.endpoint)]].filter(([,h])=>h&&h!==tokenHost);
 const trusted=hostConfirmed(tokenHost,approvedHost,confirmed);
 return <div>
 {editing?<form onSubmit={async e=>{e.preventDefault();if(!trusted)return;try{await onSave({client_id:clientId,client_secret:secret,confirmed_host:confirmed.trim().toLowerCase()||tokenHost});setEditing(false);}catch{/* Parent shows sanitized error. */}finally{setSecret('');}}}>
 <DestinationHost host={tokenHost} role="Your client secret will be sent to" documentationUrl={setup.documentation_url} approvedHost={approvedHost} confirmed={confirmed} onConfirm={setConfirmed}/>
 {others.map(([label,h])=><p key={label} style={{margin:'0 0 4px'}}>{label}: <strong>{h}</strong>{!hostIsRecognised(h,setup.documentation_url)&&' — not a known provider'}</p>)}
 <p>Register this redirect URI in your service’s application settings:</p>
 <code style={{display:'block',overflowWrap:'anywhere',marginBottom:12}}>{redirect}</code>
 <label>Client ID<input aria-label="Client ID" value={clientId} onChange={e=>setClientId(e.target.value)} required autoComplete="off" style={cs.input}/></label>
 <label>Client secret<input aria-label="Client secret" type="password" value={secret} onChange={e=>setSecret(e.target.value)} required autoComplete="new-password" style={cs.input}/></label>
 <p>Authorization: {setup.oauth?.authorization_url}<br/>Token exchange: {setup.oauth?.token_url}<br/>Permissions: {setup.oauth?.scopes.join(', ')}<br/>Agent access: {setup.method} {setup.endpoint}</p>
 <button disabled={busy||!trusted} style={{...cs.button,...cs.primary}}>Save application securely</button>
 </form>:<><button disabled={busy} style={{...cs.button,...cs.primary}} onClick={onAuthorize}>{ready?'Reconnect account':'Continue to authorization'}</button><button disabled={busy} style={{...cs.button,marginLeft:8}} onClick={()=>setEditing(true)}>Edit application</button></>}
 </div>;
}
