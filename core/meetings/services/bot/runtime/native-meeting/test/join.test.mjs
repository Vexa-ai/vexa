import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync,readFileSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSdkJoinSession} from '@vexa/join/node';
import {createNativeMeetingRuntime} from '../session.mjs';
function joinSdk(config,options){const runtime=createNativeMeetingRuntime({...options,leaveTimeoutMs:80});const session=createSdkJoinSession(runtime,config,options);session.admitted.catch(()=>runtime.dispose());return {...session,closed:runtime.closed,stop:async()=>{try{await session.leave();}catch{}finally{await runtime.dispose();}}};}
const config={meetingId:'12345678901',displayName:'Test',jwt:'private-test-jwt',onBehalfToken:'private-obf',zak:'private-zak'};
function fixture(t,scenario) {
 const dir=mkdtempSync(join(tmpdir(),'sdk-join-test-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 writeFileSync(join(dir,'libmeetingsdk.so'),'fixture only');
 const addonPath=join(dir,'fixture.cjs');
 writeFileSync(addonPath,`const fs=require('node:fs');module.exports.ZoomSDK=class {
 onAuthResult(f){this.auth=f} onMeetingStatus(f){this.status=f}
 initialize(){} authenticate(){${scenario==='auth'?'this.auth({success:false,code:11})':'this.auth({success:true})'}}
 joinMeeting(config){fs.writeFileSync(${JSON.stringify(join(dir,'config.json'))},JSON.stringify(config));${scenario==='crash'?'process.exit(4)':scenario==='native'?'throw Error("secret token")':scenario==='wait'?'this.status({status:"waiting_room"})':'this.status({status:"waiting_for_host"});this.status({status:"waiting_room"});setTimeout(()=>this.status({status:"in_meeting"}),30)'}}
 leaveMeeting(){${scenario==='stuck'?'while(true){}':'fs.writeFileSync('+JSON.stringify(join(dir,'left'))+',"yes");this.status({status:"ended"});'}}
 cleanup(){fs.writeFileSync(${JSON.stringify(join(dir,'cleaned'))},'yes')}
 };`);
 return {dir,sdkDir:dir,addonPath,timeoutMs:1500,cleanupTimeoutMs:100};
}
test('admission waits for native callback; credentials forwarded; explicit leave cleans runtime',async t=>{
 const options=fixture(t,'success'),states=[];
 const session=joinSdk(config,{...options,onState:e=>states.push(e)});
 await session.admitted;
 assert.deepEqual(states.filter(e=>e.kind==='state').map(e=>e.state),['initializing','authenticating','connecting','waiting_for_host','waiting_room','in_meeting']);
 const received=JSON.parse(readFileSync(join(options.dir,'config.json')));assert.equal(received.zak,config.zak);assert.equal(received.onBehalfToken,config.onBehalfToken);
 await session.stop();await session.stop();assert.equal(readFileSync(join(options.dir,'cleaned'),'utf8'),'yes');
 assert.ok(!JSON.stringify(states).includes('private-'));
});
for(const [scenario,expected] of [['auth','authentication_failed'],['native','native_error'],['crash','process_exit'],['wait','timeout']])test(scenario+' rejects admission',async t=>{
 const options=fixture(t,scenario);const session=joinSdk(config,{...options,timeoutMs:scenario==='wait'?200:1500});
 await assert.rejects(session.admitted,{code:expected});await session.closed;
});
test('abort during waiting room cleans and rejects',async t=>{
 const options=fixture(t,'wait'),controller=new AbortController();
 const session=joinSdk(config,{...options,signal:controller.signal,onState:e=>{if(e.state==='waiting_room')controller.abort();}});
 await assert.rejects(session.admitted,{code:'cancelled'});await session.closed;assert.equal(readFileSync(join(options.dir,'cleaned'),'utf8'),'yes');
});
test('pre-abort never creates worker',async t=>{
 const options=fixture(t,'success'),controller=new AbortController();controller.abort();
 const session=joinSdk(config,{...options,signal:controller.signal});await assert.rejects(session.admitted,{code:'cancelled'});await session.closed;
});
test('hung native leave is killed within cleanup deadline',async t=>{
 const session=joinSdk(config,fixture(t,'stuck'));await session.admitted;await session.stop();
});
test('missing runtime provides actionable failure',()=>{
 assert.throws(()=>joinSdk(config,{sdkDir:'/missing-sdk',addonPath:'/missing.node'}),{code:'runtime_missing'});
});
test('invalid config rejected before spawning',async()=>{
 const runtime={start(){throw Object.assign(Error('invalid_config'),{code:'invalid_config'});},leave:async()=>{},closed:new Promise(()=>{})};
 const session=createSdkJoinSession(runtime,{...config,meetingId:'invalid'});return assert.rejects(session.admitted,{code:'invalid_config'});
});

test('native departure preserves runtime until host disposal',async t=>{
 const options=fixture(t,'success');
 const runtime=createNativeMeetingRuntime(options);
 const session=createSdkJoinSession(runtime,config);
 await session.admitted;await session.leave();
 assert.equal(existsSync(join(options.dir,'left')),true);
 assert.equal(existsSync(join(options.dir,'cleaned')),false);
 const result=await runtime.dispose();assert.equal(result.code,0);assert.equal(result.forced,false);
 assert.equal(existsSync(join(options.dir,'cleaned')),true);
});
