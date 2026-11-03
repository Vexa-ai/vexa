import assert from 'node:assert/strict';
import {encodeAudioFrame,decodeAudioFrame} from '@vexa/capture-codec';
import {PcmDecimator,createSdkCapture} from './index.js';
function pcm(rate:number,hz:number,seconds=0.2){const bytes=new Uint8Array(Math.round(rate*seconds)*2),v=new DataView(bytes.buffer);for(let i=0;i<bytes.length/2;i++)v.setInt16(i*2,Math.round(20000*Math.sin(2*Math.PI*hz*i/rate)),true);return bytes;}
for(const rate of [32000,48000]){
 const input=pcm(rate,1000);const whole=new PcmDecimator(rate).push(input),split=new PcmDecimator(rate);const pieces=[...split.push(input.slice(0,202)),...split.push(input.slice(202))];
 assert.deepEqual(pieces,[...whole]);assert.equal(whole.length,3200);
 const rms=(x:Float32Array)=>Math.sqrt(x.slice(100).reduce((s,v)=>s+v*v,0)/(x.length-100));
 assert.ok(rms(whole)>0.4);assert.ok(rms(new PcmDecimator(rate).push(pcm(rate,12000)))<0.01);
}
let receive:any,stops=0,finalized=0;const output:any[]=[];const states:string[]=[];
const runtime={subscribe(cb:any){receive=cb;return()=>{receive=undefined;}},async startCapture(){},async stopCapture(){stops++;}};
const capture=createSdkCapture(runtime,{audioChunk:c=>output.push(c),event(){},finalize(){finalized++;}},{onState:s=>states.push(s)});
await capture.start();
for(const id of [44,55,44])receive({kind:'audio',pcm:pcm(32000,1000,0.01),sampleRate:32000,channels:1,ts:10000,userId:id,speakerName:`P${id}`,mode:'per-participant'});
assert.deepEqual(output.map(c=>c.speakerIndex),[1,2,1]);assert.equal(output[0].speakerName,'P44');assert.equal(output[0].samples.length,160);assert.equal(output[2].ts-output[0].ts,10);
receive({kind:'audio',pcm:pcm(32000,1000,0.01),sampleRate:32000,channels:1,ts:20000,userId:44,mode:'per-participant'});assert.ok(output[3].ts>19000,'silence gap retained');
receive({kind:'audio',pcm:pcm(32000,1000,0.01),sampleRate:32000,channels:1,ts:20000,userId:66,isSelf:true,mode:'per-participant'});assert.equal(output.length,4);
assert.deepEqual(states,['receiving_audio']);await capture.stop();await capture.stop();assert.equal(stops,1);assert.equal(finalized,1);
console.log('Capture: 32/48 kHz anti-aliasing, frame continuity, channel identity, timestamps, self exclusion and stop pass');

const first=output[0];const decoded=decodeAudioFrame(encodeAudioFrame(first.speakerIndex,first.ts,first.samples,first.speakerName));
assert.ok(decoded);assert.deepEqual(decoded.samples,first.samples);assert.equal(decoded.speakerName,first.speakerName);assert.equal(decoded.ts,first.ts);
let finalizedFault=0;const errors:string[]=[];
const failing=createSdkCapture({subscribe(){return()=>{};},async startCapture(){throw Error('permission_timeout');},async stopCapture(){}},{audioChunk(){},event(){},finalize(){finalizedFault++;}});
await assert.rejects(failing.start(),/permission_timeout/);await failing.stop();assert.equal(finalizedFault,1);
console.log('Shared capture wire round-trip and permission-failure cleanup pass');
