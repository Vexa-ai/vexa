// Private joining-only composition root. Config arrives via stdin, never argv.
import {createSdkJoinSession} from '@vexa/join/node';
import {createNativeMeetingRuntime} from './session.mjs';
let input='';for await(const part of process.stdin){input+=part;if(input.length>65536)throw Error('Input too large');}
const abort=new AbortController();process.once('SIGINT',()=>abort.abort());process.once('SIGTERM',()=>abort.abort());
let runtime;
try{
 runtime=createNativeMeetingRuntime({sdkDir:process.env.ZOOM_SDK_DIR,addonPath:process.env.ZOOM_SDK_ADDON});
 const joined=createSdkJoinSession(runtime,JSON.parse(input),{signal:abort.signal,timeoutMs:60000,onState:event=>console.log(JSON.stringify(event))});
 await joined.admitted;
 console.log(JSON.stringify({receipt:'native_admission_observed'}));
 await joined.leave();
 console.log(JSON.stringify({receipt:'native_departure_observed'}));
}catch(error){console.error(JSON.stringify({failure:error.code??'invalid_input'}));process.exitCode=1;}
finally{if(runtime)console.log(JSON.stringify({receipt:'runtime_closed',...await runtime.dispose()}));}
