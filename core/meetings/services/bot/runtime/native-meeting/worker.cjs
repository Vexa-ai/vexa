// Every command is checked against the sealed sdk-join.v1 / sdk-capture.v1 schema before it is acted on.
const {validCommand} = require('./protocol.cjs');
const capture = require('./capture-protocol.cjs');
let sdk, started=false, stopping=false, inMeeting=false, capturing=false, captureTimer, mode, pendingFrames=0;
const emit = (event, done) => {if(process.connected)process.send({version:1,...event}, done);else done?.();};
const stop=()=>{
  if(stopping)return;stopping=true;clearInterval(captureTimer);
  try{sdk?.stopRecording();}catch{}
  // Independent cleanup attempts: a failed leave must not skip resource destruction.
  try{sdk?.leaveMeeting();}catch{}
  try{sdk?.cleanup();}catch{}
  process.exit(0);
};
const fail=(code,nativeCode)=>{emit({kind:'failure',code,...(Number.isInteger(nativeCode)?{nativeCode}:{})},stop);};
const call=fn=>{try{fn();}catch{fail('native_error');}};
const captureFailure=code=>{capturing=false;clearInterval(captureTimer);try{sdk.stopRecording();}catch{}emit({kind:'capture-failure',code});};
function startCapture(selectedMode){
 if(!inMeeting||capturing){emit({kind:'capture-failure',code:'capture_start_failed'});return;}
 mode=selectedMode;capturing=true;
 const frame=(pcm,sampleRate,userId,ts,channels)=>{
  if(!capturing)return;
  if(pendingFrames>=8){captureFailure('backpressure');return;}
  let info;try{if(mode==='per-participant')info=sdk.getUserInfo(userId);}catch{}
  if(info?.isSelf)return;
  pendingFrames++;
  emit({kind:'audio',mode,pcm:pcm.toString('base64'),sampleRate,userId,ts,channels,...(info?.userName?{speakerName:info.userName}:{}),...(info?{isSelf:!!info.isSelf}:{})},()=>pendingFrames--);
 };
 try{
  if(mode==='per-participant')sdk.onOneWayAudioData(frame);
  else sdk.onAudioData((pcm,rate,ts,channels)=>frame(pcm,rate,0,ts,channels));
  sdk.joinAudio();
 }catch{captureFailure('capture_start_failed');return;}
 let attempts=0;
 const attempt=()=>{
  try{sdk.startRecording();clearInterval(captureTimer);emit({kind:'capture-state',state:'subscribed'});}
  catch(error){if(!String(error.message).includes('NO_PERMISSION')){captureFailure('capture_start_failed');return;}
   if(++attempts>=20){captureFailure('permission_timeout');return;}emit({kind:'capture-state',state:'permission_pending'});}
 };
 captureTimer=setInterval(attempt,1000);attempt();
}
process.on('SIGTERM',stop);process.on('SIGINT',stop);process.on('disconnect',stop);
process.on('message',message=>{
  if(stopping)return;
  if(capture.validCommand(message)&&message.kind==='capture-start'){startCapture(message.mode);return;}
  if(capture.validCommand(message)&&message.kind==='capture-stop'){capturing=false;clearInterval(captureTimer);try{sdk?.stopRecording();emit({kind:'capture-state',state:'stopped'});}catch{emit({kind:'capture-failure',code:'capture_stop_failed'});}return;}
  if(!validCommand(message))return fail('protocol_error');
  if(message.kind==='stop')return stop();
  if(message.kind==='leave'){call(()=>sdk?.leaveMeeting());return;}
  if(started)return fail('protocol_error');
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
      if(result.status==='in_meeting')inMeeting=true;
      if(result.status==='ended'){inMeeting=false;capturing=false;clearInterval(captureTimer);}
      if(result.status==='failed')return fail('join_failed',result.code);
      if(result.status==='ended'){emit({kind:'state',state:'ended'});return;}
      if(['connecting','waiting_for_host','waiting_room','in_meeting','reconnecting','disconnecting','ended'].includes(result.status))emit({kind:'state',state:result.status});
    });
    emit({kind:'state',state:'initializing'});sdk.initialize();
    emit({kind:'state',state:'authenticating'});sdk.authenticate({jwt:config.jwt});
  });
});
