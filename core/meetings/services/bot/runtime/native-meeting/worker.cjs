const {validConfig,exact} = require('./protocol.cjs');
let sdk, started=false, stopping=false;
const emit = (event, done) => {if(process.connected)process.send({version:1,...event}, done);else done?.();};
const stop=()=>{
  if(stopping)return;stopping=true;
  // Independent cleanup attempts: a failed leave must not skip resource destruction.
  try{sdk?.leaveMeeting();}catch{}
  try{sdk?.cleanup();}catch{}
  process.exit(0);
};
const fail=(code,nativeCode)=>{emit({kind:'failure',code,...(Number.isInteger(nativeCode)?{nativeCode}:{})},stop);};
const call=fn=>{try{fn();}catch{fail('native_error');}};
process.on('SIGTERM',stop);process.on('SIGINT',stop);process.on('disconnect',stop);
process.on('message',message=>{
  if(stopping)return;
  if(exact(message,['version','kind']) && message.version===1 && message.kind==='stop')return stop();
  if(exact(message,['version','kind']) && message.version===1 && message.kind==='leave'){call(()=>sdk?.leaveMeeting());return;}
  if(started || !exact(message,['version','kind','config']) || message.version!==1 || message.kind!=='start' || !validConfig(message.config))return fail('protocol_error');
  started=true;
  const config=message.config;
  try {const {ZoomSDK}=require(process.env.ZOOM_SDK_ADDON);sdk=new ZoomSDK();}catch{return fail('runtime_missing');}
  call(()=>{
    let joined=false;
    sdk.onAuthResult(result=>{
      if(stopping || joined)return;
      if(!result.success)return fail('authentication_failed',result.code);
      joined=true;emit({kind:'state',state:'connecting'});
      call(()=>sdk.joinMeeting({meetingNumber:config.meetingId,displayName:config.displayName,password:config.password||'',onBehalfToken:config.onBehalfToken||'',zak:config.zak||''}));
    });
    sdk.onMeetingStatus(result=>{
      if(stopping)return;
      if(result.status==='failed')return fail('join_failed',result.code);
      if(result.status==='ended'){emit({kind:'state',state:'ended'});return;}
      if(['connecting','waiting_for_host','waiting_room','in_meeting','reconnecting','disconnecting','ended'].includes(result.status))emit({kind:'state',state:result.status});
    });
    emit({kind:'state',state:'initializing'});sdk.initialize();
    emit({kind:'state',state:'authenticating'});sdk.authenticate({jwt:config.jwt});
  });
});
