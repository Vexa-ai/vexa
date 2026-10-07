"use client";
import {useState} from 'react';
import {connectionStyle as cs} from './connectionStyles';
import type {ConnectionSetup} from './SecretConnectionForm';
export function OAuthConnectionForm({setup,configured,ready,busy,onSave,onAuthorize}:{setup:ConnectionSetup;configured:boolean;ready:boolean;busy:boolean;onSave:(body:{client_id:string;client_secret:string})=>Promise<void>;onAuthorize:()=>void}) {
 const [editing,setEditing]=useState(!configured),[clientId,setClientId]=useState(''),[secret,setSecret]=useState('');
 const redirect=typeof window==='undefined'?'':window.location.origin+'/api/auth/callback/google';
 return <div>
 {editing?<form onSubmit={async e=>{e.preventDefault();try{await onSave({client_id:clientId,client_secret:secret});setEditing(false);}catch{/* Parent shows sanitized error. */}finally{setSecret('');}}}>
 <p>Register this redirect URI in your service’s application settings:</p>
 <code style={{display:'block',overflowWrap:'anywhere',marginBottom:12}}>{redirect}</code>
 <label>Client ID<input aria-label="Client ID" value={clientId} onChange={e=>setClientId(e.target.value)} required autoComplete="off" style={cs.input}/></label>
 <label>Client secret<input aria-label="Client secret" type="password" value={secret} onChange={e=>setSecret(e.target.value)} required autoComplete="new-password" style={cs.input}/></label>
 <p>Authorization: {setup.oauth?.authorization_url}<br/>Token exchange: {setup.oauth?.token_url}<br/>Permissions: {setup.oauth?.scopes.join(', ')}<br/>Agent access: {setup.method} {setup.endpoint}</p>
 <button disabled={busy} style={{...cs.button,...cs.primary}}>Save application securely</button>
 </form>:<><button disabled={busy} style={{...cs.button,...cs.primary}} onClick={onAuthorize}>{ready?'Reconnect account':'Continue to authorization'}</button><button disabled={busy} style={{...cs.button,marginLeft:8}} onClick={()=>setEditing(true)}>Edit application</button></>}
 </div>;
}
