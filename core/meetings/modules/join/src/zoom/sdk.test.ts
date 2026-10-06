import assert from 'assert';
import { createSdkJoinSession, type NativeJoinEvent } from './sdk';
const config={meetingId:'12345678901',displayName:'Fixture',jwt:'fixture'};
async function main(){
 let report:(e:NativeJoinEvent)=>void=()=>{},left=0,disposed=0;
 const runtime={start(_config:any,cb:any){report=cb;},leave:async()=>{left++;},closed:new Promise(()=>{}),dispose(){disposed++;}};
 const session=createSdkJoinSession(runtime,config,{timeoutMs:500});
 let admitted=false;session.admitted.then(()=>admitted=true);
 report({kind:'state',state:'waiting_room'});await Promise.resolve();assert.equal(admitted,false);
 report({kind:'state',state:'in_meeting'});await session.admitted;assert.equal(admitted,true);
 await session.leave();assert.equal(left,1);assert.equal(disposed,0,'join must not dispose the shared runtime');
 await session.leave();assert.equal(left,1);
 const aborted=new AbortController();aborted.abort();let started=false;
 const cancelled=createSdkJoinSession({...runtime,start(){started=true;}},config,{signal:aborted.signal});
 await assert.rejects(cancelled.admitted,{code:'cancelled'});assert.equal(started,false);
 console.log('SDK join port: waiting/admission, idempotent leave, runtime ownership, pre-abort pass');
}
void main();
