/** Bot-owned native process transport. Admission policy belongs to @vexa/join. */
import {fork} from 'node:child_process';
import {existsSync} from 'node:fs';
import {isAbsolute,join} from 'node:path';
import protocol from './protocol.cjs';
import captureProtocol from './capture-protocol.cjs';
const fault=code=>Object.assign(new Error(code),{code});
export function createNativeMeetingRuntime({sdkDir,addonPath,cleanupTimeoutMs=3000,leaveTimeoutMs=5000}) {
 if(!sdkDir||!addonPath||!isAbsolute(sdkDir)||!isAbsolute(addonPath)||!existsSync(join(sdkDir,'libmeetingsdk.so'))||!existsSync(addonPath))throw fault('runtime_missing');
 const listeners=new Set();let captureWait;
 const captureEvent=e=>{for(const listener of listeners){try{listener(e);}catch{}}};
 const settleCapture=(error)=>{if(!captureWait)return;clearTimeout(captureWait.timer);const w=captureWait;captureWait=undefined;error?w.reject(fault(error)):w.resolve();};
 let child,report=()=>{},resolveClosed,resolveLeft,rejectLeft,leavePromise,leaveTimer,killTimer,disposing=false,departed=false,finished=false;
 const closed=new Promise(resolve=>{resolveClosed=resolve;});
 const finish=(code,signal)=>{if(finished)return;finished=true;settleCapture('runtime_closed');captureEvent({kind:'capture-failure',code:'runtime_closed'});clearTimeout(killTimer);clearTimeout(leaveTimer);if(!departed)rejectLeft?.(fault('departure_unconfirmed'));resolveClosed({code,signal,forced:signal==='SIGKILL'});};
 const send=message=>{if(child?.connected)child.send(message,error=>{if(error)report({kind:'failure',code:'process_exit'});});};
 const dispose=()=>{
  if(disposing||finished)return closed;disposing=true;
  if(!child){finish(0,null);return closed;}
  killTimer=setTimeout(()=>child.kill('SIGKILL'),cleanupTimeoutMs);
  if(child.connected)send({version:1,kind:'stop'});else child.kill('SIGTERM');
  return closed;
 };
 return {
  closed,dispose,
  subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);},
  startCapture(mode){
   if(!child||finished||captureWait)return Promise.reject(fault('capture_start_failed'));
   return new Promise((resolve,reject)=>{captureWait={resolve,reject,state:'subscribed',timer:setTimeout(()=>settleCapture('permission_timeout'),25000)};send({version:1,kind:'capture-start',mode});});
  },
  stopCapture(){
   settleCapture('capture_stop_failed');
   if(!child||finished)return Promise.resolve();
   return new Promise((resolve,reject)=>{captureWait={resolve,reject,state:'stopped',timer:setTimeout(()=>settleCapture('capture_stop_failed'),3000)};send({version:1,kind:'capture-stop'});});
  },
  start(config,onEvent){
   if(child||disposing)throw fault('invalid_config');
   if(!protocol.validConfig(config))throw fault('invalid_config');
   report=onEvent;
   child=fork(new URL('./worker.cjs',import.meta.url),[],{env:{PATH:process.env.PATH,HOME:process.env.HOME,DISPLAY:process.env.DISPLAY,XDG_RUNTIME_DIR:process.env.XDG_RUNTIME_DIR,PULSE_SERVER:process.env.PULSE_SERVER,ZOOM_SDK_DIR:sdkDir,ZOOM_SDK_ADDON:addonPath,LD_LIBRARY_PATH:`${sdkDir}/qt_libs/Qt/lib:${sdkDir}`},stdio:['ignore','ignore','ignore','ipc']});
   child.on('message',event=>{
    if(event?.kind==='audio'||(typeof event?.kind==='string'&&event.kind.startsWith('capture-'))){
     if(!captureProtocol.valid(event)){settleCapture('invalid_capture_frame');captureEvent({kind:'capture-failure',code:'invalid_capture_frame'});return;}
     if(event.kind==='capture-failure')settleCapture(event.code);
     if(event.kind==='capture-state'&&captureWait?.state===event.state)settleCapture();
     captureEvent(event.kind==='audio'?{...event,pcm:Buffer.from(event.pcm,'base64')}:event);return;
    }
    if(!protocol.validEvent(event)){report({kind:'failure',code:'protocol_error'});void dispose();return;}
    if(event.kind==='state'&&event.state==='ended'){departed=true;clearTimeout(leaveTimer);resolveLeft?.();}
    report(event);
   });
   child.on('error',()=>{report({kind:'failure',code:'process_exit'});if(!child.pid)finish(null,null);else void dispose();});
   child.on('exit',finish);
   send({version:1,kind:'start',config});
  },
  leave(){
   if(leavePromise)return leavePromise;
   if(departed||!child)return Promise.resolve();
   if(finished)return Promise.reject(fault('departure_unconfirmed'));
   leavePromise=new Promise((resolve,reject)=>{resolveLeft=resolve;rejectLeft=reject;});
   leaveTimer=setTimeout(()=>rejectLeft(fault('leave_timeout')),leaveTimeoutMs);
   send({version:1,kind:'leave'});
   return leavePromise;
  },
 };
}
