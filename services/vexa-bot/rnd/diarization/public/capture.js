/**
 * Tab capture → 16kHz mono PCM → WebSocket /audio.
 *
 * getDisplayMedia({ audio: true, video: true }) is used because Chrome
 * requires video=true to even offer the tab option in the share dialog;
 * we drop the video track on connect. Tab audio comes back at the system
 * sample rate (typically 48000); we downsample to 16000 with a small
 * AudioWorklet to match what the bot pipeline expects.
 */

const startBtn = document.getElementById('start');
const stopBtn = document.getElementById('stop');
const statusEl = document.getElementById('status');

let mediaStream = null;
let audioContext = null;
let ws = null;
let workletNode = null;
let bytesSent = 0;
let framesSent = 0;

function setStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = 'status' + (cls ? ' ' + cls : '');
}

// AudioWorklet that downsamples to 16kHz and emits Float32 chunks
const WORKLET_CODE = `
class DownsampleWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.ratio = sampleRate / this.targetRate;
    this.acc = 0;
    this.buf = [];
    this.flushSize = 1024; // ~64ms at 16kHz
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;
    // Mix to mono by averaging available channels
    const ch0 = channels[0];
    const ch1 = channels[1];
    const length = ch0.length;
    for (let i = 0; i < length; i++) {
      let s = ch0[i];
      if (ch1) s = (s + ch1[i]) * 0.5;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        this.buf.push(s);
        if (this.buf.length >= this.flushSize) {
          this.port.postMessage(new Float32Array(this.buf));
          this.buf = [];
        }
      }
    }
    return true;
  }
}
registerProcessor('downsample-worklet', DownsampleWorklet);
`;

async function buildWorkletURL() {
  const blob = new Blob([WORKLET_CODE], { type: 'application/javascript' });
  return URL.createObjectURL(blob);
}

async function start() {
  startBtn.disabled = true;
  setStatus('Requesting tab capture (pick a tab and tick "Share tab audio")...');

  try {
    mediaStream = await navigator.mediaDevices.getDisplayMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: true, // required for tab option in some browsers; dropped below
    });
  } catch (err) {
    setStatus('Capture cancelled or denied: ' + err.message, 'err');
    startBtn.disabled = false;
    return;
  }

  const audioTracks = mediaStream.getAudioTracks();
  if (audioTracks.length === 0) {
    setStatus('No audio track in the captured stream. Re-share the tab and check the "Share tab audio" box.', 'err');
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
    startBtn.disabled = false;
    return;
  }

  // Drop video tracks
  mediaStream.getVideoTracks().forEach(t => { t.stop(); mediaStream.removeTrack(t); });

  setStatus('Tab captured. Connecting to harness...');

  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/audio';
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = (e) => reject(new Error('WebSocket error'));
  }).catch(err => {
    setStatus('Could not connect to /audio: ' + err.message, 'err');
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
    ws = null;
    startBtn.disabled = false;
    return;
  });
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  audioContext = new AudioContext({ sampleRate: 48000 });
  await audioContext.audioWorklet.addModule(await buildWorkletURL());

  const source = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, 'downsample-worklet');
  workletNode.port.onmessage = (ev) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const samples = ev.data; // Float32Array, 16kHz
    const buf = new ArrayBuffer(8 + samples.byteLength);
    const view = new DataView(buf);
    view.setFloat64(0, Date.now(), true);
    new Float32Array(buf, 8).set(samples);
    ws.send(buf);
    bytesSent += buf.byteLength;
    framesSent += 1;
  };

  source.connect(workletNode);
  // Don't connect to destination — we'd echo the tab audio back to the user.

  setStatus('Capturing and streaming. See /dashboard for live output.', 'ok');
  stopBtn.disabled = false;

  // Status loop
  const tick = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      clearInterval(tick);
      return;
    }
    setStatus(
      'Streaming.  frames=' + framesSent +
      '  bytes=' + bytesSent +
      '  sr=' + audioContext.sampleRate + 'Hz (downsampled to 16kHz mono)' +
      '\nSee /dashboard for live diarized transcript.',
      'ok',
    );
  }, 1000);

  // Detect user clicking the browser's "Stop sharing" button
  mediaStream.getAudioTracks()[0].onended = () => stop();
}

function stop() {
  if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  ws = null;
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  stopBtn.disabled = true;
  startBtn.disabled = false;
  setStatus('Stopped. frames=' + framesSent + ' bytes=' + bytesSent);
}

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
