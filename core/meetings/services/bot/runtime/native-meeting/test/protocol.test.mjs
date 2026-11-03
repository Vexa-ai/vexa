// The runtime's IPC validators conform to sdk-join.v1 and sdk-capture.v1 (ADR-0039): every golden is
// accepted, every golden with an unknown property is refused, and the worker refuses a command the schema
// refuses. The validators are compiled from the sealed schemas, so this holds the wiring, not a hand copy.
import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {mkdtempSync,readFileSync,readdirSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import protocol from '../protocol.cjs';
import capture from '../capture-protocol.cjs';

const contracts=new URL('../../../../../contracts/',import.meta.url);
const runtime={
  'sdk-join.v1':{Config:protocol.validConfig,Command:protocol.validCommand,Event:protocol.validEvent},
  'sdk-capture.v1':{Command:capture.validCommand,Event:capture.valid},
};
const goldens=name=>readdirSync(new URL(`${name}/golden/`,contracts)).filter(n=>n.endsWith('.json')).map(n=>[n,n.split('.')[0],JSON.parse(readFileSync(new URL(`${name}/golden/${n}`,contracts)))]);

for(const name of Object.keys(runtime))test(`${name}: the runtime accepts every golden and refuses an unknown property`,()=>{
  const cases=goldens(name);assert.ok(cases.length>=4);
  for(const [file,shape,value] of cases){
    const valid=runtime[name][shape];assert.ok(valid,`${file}: the runtime has no validator for ${shape}`);
    assert.equal(valid(value),true,file);
    assert.equal(valid({...value,unexpected:'not in contract'}),false,`${file} + unknown property`);
    if(value.config)assert.equal(valid({...value,config:{...value.config,unexpected:1}}),false,`${file} + unknown config property`);
  }
});

test('the runtime refuses what the schemas refuse',()=>{
  for(const invalid of [null,[],{},{version:2,kind:'stop'},{version:1,kind:'capture-state',state:'ready'},{version:1,kind:'capture-failure',code:'arbitrary'}]){
    assert.equal(capture.valid(invalid),false);assert.equal(protocol.validEvent(invalid),false);
  }
  assert.equal(protocol.validConfig({meetingId:'1234',displayName:'x',jwt:'j'}),false);
  assert.equal(protocol.validEvent({version:1,kind:'failure',code:'join_failed',nativeCode:1.5}),false);
  assert.equal(capture.validCommand({version:1,kind:'capture-start',mode:'both'}),false);
});

test('neither validator module carries a hand copy of the wire vocabulary',()=>{
  const source=['../protocol.cjs','../capture-protocol.cjs'].map(p=>readFileSync(new URL(p,import.meta.url),'utf8')).join('\n');
  for(const word of ['waiting_for_host','ended_before_admission','permission_pending','invalid_capture_frame','per-participant'])
    assert.ok(!source.includes(`'${word}'`),`${word} is spelled in the runtime; take it from the schema`);
});

test('the worker refuses a command with an unknown property as a protocol error',async t=>{
  const dir=mkdtempSync(join(tmpdir(),'sdk-protocol-test-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const addon=join(dir,'fixture.cjs');writeFileSync(addon,'module.exports.ZoomSDK=class{}');
  const child=fork(new URL('../worker.cjs',import.meta.url),[],{env:{ZOOM_SDK_ADDON:addon},stdio:['ignore','ignore','ignore','ipc']});
  const reply=new Promise(resolve=>child.once('message',resolve));
  child.send({version:1,kind:'leave',unexpected:true});
  assert.deepEqual(await reply,{version:1,kind:'failure',code:'protocol_error'});
  await new Promise(resolve=>child.once('exit',resolve));
});
