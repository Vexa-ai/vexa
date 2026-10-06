/** Bot-owned native process transport. Admission policy belongs to @vexa/join. */
import {fork} from 'node:child_process';
import {existsSync} from 'node:fs';
import {isAbsolute,join} from 'node:path';
import protocol from './protocol.cjs';
const fault=code=>Object.assign(new Error(code),{code});
export function createNativeMeetingRuntime({sdkDir,addonPath,cleanupTimeoutMs=3000,leaveTimeoutMs=5000}) {
 if(!sdkDir||!addonPath||!isAbsolute(sdkDir)||!isAbsolute(addonPath)||!existsSync(join(sdkDir,'libmeetingsdk.so'))||!existsSync(addonPath))throw fault('runtime_missing');
 let child,report=()=>{},resolveClosed,resolveLeft,rejectLeft,leavePromise,leaveTimer,killTimer,disposing=false,departed=false,finished=false;
 const closed=new Promise(resolve=>{resolveClosed=resolve;});
 const finish=(code,signal)=>{if(finished)return;finished=true;clearTimeout(killTimer);clearTimeout(leaveTimer);if(!departed)rejectLeft?.(fault('departure_unconfirmed'));resolveClosed({code,signal,forced:signal==='SIGKILL'});};
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
  start(config,onEvent){
   if(child||disposing)throw fault('invalid_config');
   if(!protocol.validConfig(config))throw fault('invalid_config');
   report=onEvent;
   child=fork(new URL('./worker.cjs',import.meta.url),[],{env:{PATH:process.env.PATH,HOME:process.env.HOME,DISPLAY:process.env.DISPLAY,ZOOM_SDK_DIR:sdkDir,ZOOM_SDK_ADDON:addonPath,LD_LIBRARY_PATH:`${sdkDir}/qt_libs/Qt/lib:${sdkDir}`},stdio:['ignore','ignore','ignore','ipc']});
   child.on('message',event=>{
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
