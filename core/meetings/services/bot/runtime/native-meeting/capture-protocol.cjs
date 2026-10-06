const {exact}=require('./protocol.cjs');
const states=['permission_pending','subscribed','stopped'];
const failures=['capture_start_failed','capture_stop_failed','permission_timeout','backpressure','runtime_closed','invalid_capture_frame'];
function valid(e){if(!e||e.version!==1)return false;
 if(e.kind==='capture-state')return exact(e,['version','kind','state'])&&states.includes(e.state);
 if(e.kind==='capture-failure')return exact(e,['version','kind','code'])&&failures.includes(e.code);
 return exact(e,['version','kind','mode','pcm','sampleRate','channels','ts','userId','speakerName','isSelf'])&&e.kind==='audio'&&['mixed','per-participant'].includes(e.mode)&&typeof e.pcm==='string'&&e.pcm.length<=262144&&e.pcm.length%4===0&&/^[A-Za-z0-9+/]*={0,2}$/.test(e.pcm)&&[32000,48000].includes(e.sampleRate)&&e.channels===1&&Number.isFinite(e.ts)&&Number.isInteger(e.userId)&&e.userId>=0&&(e.speakerName===undefined||typeof e.speakerName==='string')&&(e.isSelf===undefined||typeof e.isSelf==='boolean');}
module.exports={valid,states,failures};
