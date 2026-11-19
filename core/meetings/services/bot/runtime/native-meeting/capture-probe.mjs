// Owned-meeting audio probe: counters only, no PCM or participant names persisted.
import {createSdkJoinSession} from '@vexa/join/node';
import {createSdkCapture} from '@vexa/zoom-sdk-capture';
import {createNativeMeetingRuntime} from './session.mjs';
let input='';for await(const part of process.stdin){input+=part;if(input.length>65536)throw Error('Input too large');}
let runtime,joined,capture,frames=0,samples=0,peak=0,failed=false;const channels=new Set(),inputRates=new Set();let namedFrames=0;
try{
 runtime=createNativeMeetingRuntime({sdkDir:process.env.ZOOM_SDK_DIR,addonPath:process.env.ZOOM_SDK_ADDON});
 runtime.subscribe(event=>{if(event.kind==='audio')inputRates.add(event.sampleRate);});
 joined=createSdkJoinSession(runtime,JSON.parse(input),{timeoutMs:60000,onState:e=>console.log(JSON.stringify(e))});await joined.admitted;
 capture=createSdkCapture(runtime,{audioChunk:c=>{frames++;if(c.speakerName)namedFrames++;samples+=c.samples.length;channels.add(c.speakerIndex);for(const s of c.samples)peak=Math.max(peak,Math.abs(s));},event(){},finalize(){}},{onState:state=>console.log(JSON.stringify({capture:state})),onError:code=>{failed=true;console.log(JSON.stringify({captureFailure:code}));},noAudioMs:20000});
 await capture.start();await new Promise(resolve=>setTimeout(resolve,15000));
 console.log(JSON.stringify({receipt:'capture_counters',frames,samples,channels:channels.size,namedFrames,inputRates:[...inputRates],sampleRate:16000,peak,nonSilent:peak>0.001}));
 if(!frames||failed)process.exitCode=1;
}catch(error){console.log(JSON.stringify({failure:error.code??error.message}));process.exitCode=1;}
finally{try{await capture?.stop();}finally{try{await joined?.leave();}finally{if(runtime)console.log(JSON.stringify({receipt:'runtime_closed',...await runtime.dispose()}));}}}
