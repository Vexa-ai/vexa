// Supply one JSON JoinConfig on stdin. Never pass credentials in argv.
import {joinSdk} from '../src/node.mjs';
let input='';for await (const part of process.stdin){input+=part;if(input.length>65536)throw Error('Input too large');}
const signal=new AbortController();process.once('SIGINT',()=>signal.abort());process.once('SIGTERM',()=>signal.abort());
try {
 const session=joinSdk(JSON.parse(input),{sdkDir:process.env.ZOOM_SDK_DIR,addonPath:process.env.ZOOM_SDK_ADDON,signal:signal.signal,onState:e=>console.log(JSON.stringify(e))});
 await session.admitted;
 console.log(JSON.stringify({receipt:'native_admission_observed'}));
 await session.closed;
} catch(error){console.error(error.code || 'invalid_input');process.exitCode=1;}
