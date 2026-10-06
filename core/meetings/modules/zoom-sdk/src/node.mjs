import { fork } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';
import protocol from './protocol.cjs';
export class JoinError extends Error { constructor(code) { super(code === 'runtime_missing' ? 'Install the separately downloaded Linux SDK and build the Vexa addon; supply sdkDir and addonPath. No SDK is bundled.' : code); this.name='JoinError'; this.code=code; } }
export function joinSdk(config, options) { return createJoin(config, options); }
// Injection is internal to tests, not exposed by package exports.
export function createJoin(config, {sdkDir,addonPath,onState,signal,timeoutMs=120000,cleanupTimeoutMs=3000}, spawn=fork) {
  if (!protocol.validConfig(config) || !Number.isFinite(timeoutMs) || timeoutMs <= 0 || !Number.isFinite(cleanupTimeoutMs) || cleanupTimeoutMs <= 0) throw new JoinError('invalid_config');
  if (!sdkDir || !addonPath || !isAbsolute(sdkDir) || !isAbsolute(addonPath) || !existsSync(join(sdkDir,'libmeetingsdk.so')) || !existsSync(addonPath)) throw new JoinError('runtime_missing');
  let resolveAdmission, rejectAdmission, resolveClosed, child, deadline, killTimer, settled=false, stopping=false, finished=false;
  const admitted=new Promise((resolve,reject)=>{resolveAdmission=resolve;rejectAdmission=reject;});
  // Keep cancellation-before-await from causing an unhandled rejection. Callers still receive rejection.
  admitted.catch(()=>{});
  const closed=new Promise(resolve=>{resolveClosed=resolve;});
  const notify=e=>{try {onState?.(e);} catch { /* Observer cannot prevent cleanup. */ }};
  const reject=code=>{if(!settled){settled=true;rejectAdmission(new JoinError(code));}};
  const finish=()=>{if(finished)return;finished=true;clearTimeout(deadline);clearTimeout(killTimer);signal?.removeEventListener('abort',abort);resolveClosed();};
  const shutdown=(code)=>{
    if(stopping || finished)return closed;
    stopping=true;clearTimeout(deadline);reject(code);
    if(!child){finish();return closed;}
    killTimer=setTimeout(()=>child.kill('SIGKILL'),cleanupTimeoutMs);
    if(child.connected) child.send({version:1,kind:'stop'},error=>{if(error)child.kill('SIGTERM');});
    else child.kill('SIGTERM');
    return closed;
  };
  const fail=code=>{notify({version:1,kind:'failure',code});shutdown(code);};
  const abort=()=>fail('cancelled');
  if(signal?.aborted){abort();return {admitted,closed,stop:()=>closed};}
  try {
    child=spawn(new URL('./worker.cjs',import.meta.url),[],{
      env:{PATH:process.env.PATH,HOME:process.env.HOME,DISPLAY:process.env.DISPLAY,ZOOM_SDK_DIR:sdkDir,ZOOM_SDK_ADDON:addonPath,LD_LIBRARY_PATH:`${sdkDir}/qt_libs/Qt/lib:${sdkDir}`},
      // Native diagnostics may include meeting data. Do not forward them to application logs.
      stdio:['ignore','ignore','ignore','ipc']});
    child.on('message',event=>{
      if(stopping || finished)return;
      if(!protocol.validEvent(event)){fail('protocol_error');return;}
      notify(event);
      if(event.kind==='failure'){shutdown(event.code);return;}
      if(event.state==='in_meeting'&&!settled){settled=true;clearTimeout(deadline);resolveAdmission();}
      if(event.state==='ended')shutdown('ended_before_admission');
    });
    child.on('error',()=>{fail('process_exit');if(!child.pid)finish();});
    child.on('exit',()=>{if(!stopping){notify({version:1,kind:'failure',code:'process_exit'});reject('process_exit');}finish();});
    signal?.addEventListener('abort',abort,{once:true});
    deadline=setTimeout(()=>fail('timeout'),timeoutMs);
    child.send({version:1,kind:'start',config},error=>{if(error)fail('process_exit');});
  } catch {fail('process_exit'); if(!child)finish();}
  return {admitted,closed,stop:()=>shutdown('left')};
}
