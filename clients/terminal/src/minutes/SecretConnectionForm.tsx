"use client";
import {useState} from 'react';
import { connectionStyle as cs } from './connectionStyles';
import {type as ty} from './tokens';
import { DestinationHost, hostConfirmed } from './DestinationHost';
import { hostOf } from './connectionHosts';
export type ConnectionSetup={oauth?:{authorization_url:string;token_url:string;scopes:string[];token_auth?:string}|null;documentation_url?:string;endpoint:string;method:string;secret_label:string;fields:{name:string;label:string;location:string}[]};
export function SecretConnectionForm({onSave,busy,setup,hasCredential=false,approvedHost}:{onSave:(body:Record<string,unknown>)=>Promise<void>;busy:boolean;setup?:ConnectionSetup;hasCredential?:boolean;approvedHost?:string}) {
 const [value,setValue]=useState(''),[endpoint,setEndpoint]=useState(''),[header,setHeader]=useState('Authorization'),[scheme,setScheme]=useState('bearer'),[method,setMethod]=useState('GET');
 const [fields,setFields]=useState<Record<string,string>>({});
 const [confirmed,setConfirmed]=useState('');
 const prepared=!!setup?.endpoint;
 // A prepared setup may come from the agent: its host must be confirmed on first use (M3). An
 // endpoint the person types here is their own choice and is shown, not re-confirmed.
 const host=prepared?hostOf(setup!.endpoint):hostOf(endpoint);
 const trusted=!prepared||hostConfirmed(host,approvedHost,confirmed);
 const input=cs.input;
 return <form onSubmit={async e=>{e.preventDefault();if(!trusted)return;try{await onSave(prepared?{value,fields,...(host?{confirmed_host:confirmed.trim().toLowerCase()||host}:{})}:{value,endpoint,header,scheme,method});}finally{setValue('');}}}>
  {host&&<DestinationHost host={host} role="Your secret will be sent to" documentationUrl={setup?.documentation_url} approvedHost={prepared?approvedHost:host} confirmed={confirmed} onConfirm={setConfirmed}/>}
  <label style={ty.meta}>{prepared?setup!.secret_label:"Secret value"}<textarea aria-label={prepared?setup!.secret_label:"Secret value"} required={!hasCredential} value={value} onChange={e=>setValue(e.target.value)} autoComplete="off" spellCheck={false} maxLength={65536} style={{...input,WebkitTextSecurity:'disc'} as React.CSSProperties}/></label>
  {hasCredential&&<p style={ty.meta}>Your saved credential will be reused. Enter a replacement only if it changed.</p>}
  {prepared?<>
   <p style={ty.meta}>Prepared connection: {setup!.method} {setup!.endpoint}</p>
   {setup!.fields.map(f=><label key={f.name} style={ty.meta}>{f.label}<input aria-label={f.label} required maxLength={2000} value={fields[f.name]||''} onChange={e=>setFields({...fields,[f.name]:e.target.value})} style={input}/></label>)}
   <p style={ty.meta}>Saving approves this endpoint and these fields for agent use. {setup!.method==='POST'?'Requests can change data in this service.':''}</p>
  </>:<><label style={ty.meta}>Exact HTTPS endpoint (optional)<input aria-label="HTTPS endpoint" value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://api.example.com/v1/resource" style={input}/></label>
  {endpoint&&<>
   <label style={ty.meta}>Authentication header<input aria-label="Authentication header" value={header} onChange={e=>setHeader(e.target.value)} style={input}/></label>
   <label style={ty.meta}>Value format<select aria-label="Value format" value={scheme} onChange={e=>setScheme(e.target.value)} style={input}><option value="bearer">Bearer token</option><option value="raw">Raw API key</option></select></label>
   <label style={ty.meta}>Allowed method<select aria-label="Allowed method" value={method} onChange={e=>setMethod(e.target.value)} style={input}><option>GET</option><option>POST</option></select></label>
   <p style={ty.meta}>Saving authorizes the agent to use this credential at this exact endpoint. {method==='POST'?'POST can change data in that service.':'GET is intended for reading data.'} Redirects and private network destinations are blocked.</p>
  </>}
  </>}
  <button disabled={busy||(!value&&!hasCredential)||!trusted} style={{...cs.button,...cs.primary,width:'100%'}}>Save securely</button>
 </form>;
}
