"use client";
/** M3: the destination of a secret, shown as the form's dominant element, with the unknown-host
 *  warning and the typed first-use confirmation. See connectionHosts.ts for the rule. */
import { connectionStyle as cs } from './connectionStyles';
import { type as ty } from './tokens';
import { hostIsRecognised, needsConfirmation } from './connectionHosts';

export function DestinationHost({host,role,documentationUrl,approvedHost,confirmed,onConfirm}:{
  host:string; role:string; documentationUrl?:string; approvedHost?:string;
  confirmed:string; onConfirm:(value:string)=>void;
}) {
  if(!host)return null;
  const recognised=hostIsRecognised(host,documentationUrl);
  const confirm=needsConfirmation(host,approvedHost);
  return <div data-destination-host={host} style={{border:'1px solid var(--line2)',borderRadius:8,padding:12,margin:'0 0 12px'}}>
    <p style={{...ty.meta,margin:0}}>{role}</p>
    <p aria-label="Destination host" style={{fontFamily:'var(--mono, monospace)',fontSize:20,fontWeight:600,margin:'4px 0',overflowWrap:'anywhere'}}>{host}</p>
    {!recognised&&<p role="alert" style={{...ty.meta,color:'var(--danger)',margin:'4px 0'}}>This host is not a known provider{documentationUrl?' and is not on the same site as the service’s documentation':''}. Only continue if you expected your secret to go here.</p>}
    {confirm&&<label style={{...ty.meta,display:'block',marginTop:8}}>Type the host to confirm you trust it
      <input aria-label="Confirm destination host" value={confirmed} onChange={e=>onConfirm(e.target.value)} autoComplete="off" spellCheck={false} placeholder={host} style={cs.input}/>
    </label>}
  </div>;
}

export function hostConfirmed(host:string,approvedHost:string|undefined,confirmed:string):boolean {
  return !needsConfirmation(host,approvedHost)||confirmed.trim().toLowerCase()===host;
}
