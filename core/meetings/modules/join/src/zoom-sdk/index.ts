/** Join/admission policy over an injected native session. No process or capture ownership. */
export interface NativeJoinConfig { meetingId: string; displayName: string; jwt: string; password?: string; onBehalfToken?: string; zak?: string; }
export type NativeJoinEvent = { kind: 'state'; state: string } | { kind: 'failure'; code: string; nativeCode?: number };
export interface NativeJoinPort {
  start(config: NativeJoinConfig, report: (event: NativeJoinEvent) => void): void;
  /** Resolve on observed departure; reject if departure cannot be confirmed. */
  leave(): Promise<void>;
  readonly closed: Promise<unknown>;
}
export interface NativeJoinOptions { timeoutMs?: number; signal?: AbortSignal; onState?: (event: NativeJoinEvent) => void; }
export interface NativeJoinSession { admitted: Promise<void>; leave(): Promise<void>; onRemoval(callback: () => void): () => void; }
export class NativeJoinError extends Error { constructor(public code: string) { super(code);this.name='NativeJoinError'; } }
export function createSdkJoinSession(runtime: NativeJoinPort, config: NativeJoinConfig, options: NativeJoinOptions = {}): NativeJoinSession {
  const timeoutMs=options.timeoutMs ?? 120000;
  if (!Number.isFinite(timeoutMs) || timeoutMs<=0) throw new NativeJoinError('invalid_config');
  let resolveAdmission!:()=>void, rejectAdmission!:(error:Error)=>void;
  let settled=false, admittedOnce=false, left=false, leavePromise:Promise<void>|undefined;
  const removals=new Set<()=>void>();
  const admitted=new Promise<void>((resolve,reject)=>{resolveAdmission=resolve;rejectAdmission=reject;});
  admitted.catch(()=>{});
  const notify=(event:NativeJoinEvent)=>{try{options.onState?.(event);}catch{}};
  const reject=(code:string)=>{if(!settled){settled=true;clearTimeout(deadline);rejectAdmission(new NativeJoinError(code));}};
  const leave=()=>{if(!leavePromise){left=true;reject('left');options.signal?.removeEventListener('abort',abort);leavePromise=runtime.leave();}return leavePromise;};
  const fail=(code:string)=>{notify({kind:'failure',code});reject(code);void leave().catch(()=>{});};
  const abort=()=>fail('cancelled');
  const deadline=setTimeout(()=>fail('timeout'),timeoutMs);
  runtime.closed.then(()=>{clearTimeout(deadline);options.signal?.removeEventListener('abort',abort);reject('process_exit');if(admittedOnce&&!left)for(const cb of removals){try{cb();}catch{}};});
  if(options.signal?.aborted){abort();return {admitted,leave,onRemoval:cb=>{removals.add(cb);return()=>removals.delete(cb);}};}
  options.signal?.addEventListener('abort',abort,{once:true});
  try {runtime.start(config,event=>{
    if(left)return;
    notify(event);
    if(event.kind==='failure'){reject(event.code);if(admittedOnce)for(const cb of removals){try{cb();}catch{}}void leave().catch(()=>{});return;}
    if(event.state==='in_meeting'&&!settled){settled=true;admittedOnce=true;clearTimeout(deadline);resolveAdmission();}
    if(event.state==='ended'){
      reject('ended_before_admission');
      options.signal?.removeEventListener('abort',abort);
      if(admittedOnce)for(const cb of removals){try{cb();}catch{}}
      left=true;
    }
  });} catch(error) {fail(error instanceof NativeJoinError?error.code:((error as any)?.code ?? 'native_error'));}
  return {admitted,leave,onRemoval:cb=>{removals.add(cb);return()=>removals.delete(cb);}};
}
