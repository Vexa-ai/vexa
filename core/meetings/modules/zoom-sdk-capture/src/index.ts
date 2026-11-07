import type {CaptureV1Sink} from '@vexa/capture-codec';
export type CaptureMode = 'per-participant' | 'mixed';
export interface SdkAudioFrame {kind:'audio'; pcm:Uint8Array; sampleRate:number; channels:number; ts:number; userId:number; speakerName?:string; isSelf?:boolean; mode:CaptureMode;}
export type SdkCaptureEvent = SdkAudioFrame | {kind:'capture-state';state:string} | {kind:'capture-failure';code:string};
export interface SdkCapturePort {subscribe(listener:(event:SdkCaptureEvent)=>void):()=>void; startCapture(mode:CaptureMode):Promise<void>;stopCapture():Promise<void>;}
/** Stateful anti-aliasing decimator. One instance per participant; 32/48 kHz mono to 16 kHz. */
export class PcmDecimator {
 private history=new Float64Array(63); private cursor=0; private count=0; private taps:Float64Array;
 constructor(readonly sampleRate:number){
  if(sampleRate!==32000&&sampleRate!==48000)throw Error('unsupported_sample_rate');
  const cutoff=7200/sampleRate;this.taps=Float64Array.from({length:63},(_,i)=>{const x=i-31;return (x===0?2*cutoff:Math.sin(2*Math.PI*cutoff*x)/(Math.PI*x))*(0.54-0.46*Math.cos(2*Math.PI*i/62));});
  const sum=this.taps.reduce((a,b)=>a+b,0);this.taps=this.taps.map(x=>x/sum);
 }
 push(bytes:Uint8Array):Float32Array{
  if(bytes.byteLength%2)throw Error('invalid_pcm');const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),out:number[]=[];
  for(let i=0;i<bytes.byteLength;i+=2){this.history[this.cursor]=view.getInt16(i,true)/32768;this.cursor=(this.cursor+1)%63;
   if(++this.count%(this.sampleRate/16000)===0){let x=0;for(let j=0;j<63;j++)x+=this.taps[j]*this.history[(this.cursor-1-j+126)%63];out.push(x);}}
  return Float32Array.from(out);
 }
}
export function createSdkCapture(runtime:SdkCapturePort,sink:CaptureV1Sink,options:{mode?:CaptureMode;onState?:(state:string)=>void;onError?:(code:string)=>void;noAudioMs?:number}={}){
 const mode=options.mode??'per-participant';const tracks=new Map<number,{index:number;resampler:PcmDecimator;nextTs:number}>();
 let stopPromise:Promise<void>|undefined;
 let unsubscribe:(()=>void)|undefined,timer:ReturnType<typeof setTimeout>|undefined,active=false,started=false,finalized=false,ready=false;
 const fault=(code:string)=>{try{options.onError?.(code);}finally{void stop().catch(()=>{});}};
 const watchdog=()=>{clearTimeout(timer);timer=setTimeout(()=>fault(ready?'audio_stalled':'no_audio'),options.noAudioMs??15000);};
 function stop():Promise<void>{if(stopPromise)return stopPromise;finalized=true;active=false;clearTimeout(timer);unsubscribe?.();stopPromise=(async()=>{try{if(started)await runtime.stopCapture();}finally{await sink.finalize();}})();return stopPromise;}
 return {
  async start(){
   if(started||finalized)throw Error('capture_already_started');started=true;active=true;
   unsubscribe=runtime.subscribe(event=>{
    if(!active)return;
    if(event.kind==='capture-failure'){fault(event.code);return;}
    if(event.kind==='capture-state'){options.onState?.(event.state);return;}
    if(event.mode!==mode||event.isSelf)return;
    try{
     if(event.channels!==1||!Number.isFinite(event.ts)||!Number.isInteger(event.userId))throw Error('invalid_audio_frame');
     let track=tracks.get(event.userId);
     if(!track){if(tracks.size>=998)throw Error('too_many_tracks');track={index:mode==='mixed'?999:tracks.size+1,resampler:new PcmDecimator(event.sampleRate),nextTs:event.ts-event.pcm.byteLength/2/event.sampleRate*1000-31000/event.sampleRate};tracks.set(event.userId,track);}
     if(track.resampler.sampleRate!==event.sampleRate)throw Error('sample_rate_changed');
     const frameStart=event.ts-event.pcm.byteLength/2/event.sampleRate*1000-31000/event.sampleRate;
     // Preserve genuine silence gaps; never close them by concatenating participant turns.
     if(frameStart-track.nextTs>100){track.resampler=new PcmDecimator(event.sampleRate);track.nextTs=frameStart;}
     const samples=track.resampler.push(event.pcm);if(!samples.length)return;
     sink.audioChunk({speakerId:`sdk-${event.userId}`,speakerIndex:track.index,samples,ts:track.nextTs,...(event.speakerName?{speakerName:event.speakerName}:{})});
     track.nextTs+=samples.length/16;watchdog();if(!ready){ready=true;options.onState?.('receiving_audio');}
    }catch(error){fault((error as Error).message);}
   });
   try{await runtime.startCapture(mode);if(active)watchdog();}catch(error){await stop();throw error;}
  },stop,
 };
}
