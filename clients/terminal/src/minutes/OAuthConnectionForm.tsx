"use client";
import {useState} from 'react';
import {connectionStyle as cs} from './connectionStyles';
import type {ConnectionSetup} from './SecretConnectionForm';
import { DestinationHost, hostConfirmed } from './DestinationHost';
import { hostIsRecognised, hostOf } from './connectionHosts';
export function OAuthConnectionForm({setup,configured,ready,busy,onSave,onAuthorize,approvedHost}:{setup:ConnectionSetup;configured:boolean;ready:boolean;busy:boolean;onSave:(body:{client_id:string;client_secret:string;confirmed_host?:string;confirmed_hosts?:string[]})=>Promise<void>;onAuthorize:()=>void;approvedHost?:string}) {
 const [editing,setEditing]=useState(!configured),[clientId,setClientId]=useState(''),[secret,setSecret]=useState('');
 const [confirmed,setConfirmed]=useState(''),[confirmedEndpoint,setConfirmedEndpoint]=useState('');
 const redirect=typeof window==='undefined'?'':window.location.origin+'/api/auth/callback/google';
 // Two hosts receive a credential (M3): the token endpoint gets the client secret, and the service
 // endpoint gets the person's access token on every agent request. Each is read and confirmed.
 const tokenHost=hostOf(setup.oauth?.token_url);
 const endpointHost=hostOf(setup.endpoint);
 const separateEndpoint=!!endpointHost&&endpointHost!==tokenHost;
 const signIn=hostOf(setup.oauth?.authorization_url);
 const trusted=hostConfirmed(tokenHost,approvedHost,confirmed)&&(!separateEndpoint||hostConfirmed(endpointHost,approvedHost,confirmedEndpoint));
 const typedEndpoint=confirmedEndpoint.trim().toLowerCase();
 return <div>
 {editing?<form onSubmit={async e=>{e.preventDefault();if(!trusted)return;try{await onSave({client_id:clientId,client_secret:secret,confirmed_host:confirmed.trim().toLowerCase()||tokenHost,...(separateEndpoint&&typedEndpoint?{confirmed_hosts:[typedEndpoint]}:{})});setEditing(false);}catch{/* Parent shows sanitized error. */}finally{setSecret('');}}}>
 <DestinationHost host={tokenHost} role="Your client secret will be sent to" approvedHost={approvedHost} confirmed={confirmed} onConfirm={setConfirmed}/>
 {separateEndpoint&&<DestinationHost host={endpointHost} role="Your account’s access token will be sent to, on every agent request" approvedHost={approvedHost} confirmed={confirmedEndpoint} onConfirm={setConfirmedEndpoint} name="Service endpoint host" confirmName="Confirm service endpoint host"/>}
 {signIn&&signIn!==tokenHost&&<p style={{margin:'0 0 4px'}}>Sign-in page: <strong>{signIn}</strong>{!hostIsRecognised(signIn)&&' — not a known provider'}</p>}
 <p>Register this redirect URI in your service’s application settings:</p>
 <code style={{display:'block',overflowWrap:'anywhere',marginBottom:12}}>{redirect}</code>
 <label>Client ID<input aria-label="Client ID" value={clientId} onChange={e=>setClientId(e.target.value)} required autoComplete="off" style={cs.input}/></label>
 <label>Client secret<input aria-label="Client secret" type="password" value={secret} onChange={e=>setSecret(e.target.value)} required autoComplete="new-password" style={cs.input}/></label>
 <p>Authorization: {setup.oauth?.authorization_url}<br/>Token exchange: {setup.oauth?.token_url}<br/>Permissions: {setup.oauth?.scopes.join(', ')}<br/>Agent access: {setup.method} {setup.endpoint}</p>
 <button disabled={busy||!trusted} style={{...cs.button,...cs.primary}}>Save application securely</button>
 </form>:<><button disabled={busy} style={{...cs.button,...cs.primary}} onClick={onAuthorize}>{ready?'Reconnect account':'Continue to authorization'}</button><button disabled={busy} style={{...cs.button,marginLeft:8}} onClick={()=>setEditing(true)}>Edit application</button></>}
 </div>;
}
