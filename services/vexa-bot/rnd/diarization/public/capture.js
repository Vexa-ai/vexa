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

// AudioWorklet: lowpass-then-decimate from the AudioContext's native rate
// (typically 48000 Hz) down to 16000 Hz, mono.
//
// The previous implementation took every ~3rd sample with NO anti-aliasing
// filter. At 48k→16k that aliases everything above 8 kHz back into the speech
// band, corrupting the 4–8 kHz region wespeaker uses for speaker
// discrimination. Threshold tuning on Piper (clean) audio didn't transfer to
// live tab capture because the live audio embeddings were drifting in a way
// the synthetic corpus couldn't reproduce.
//
// Fix: 63-tap windowed-sinc lowpass with cutoff ~7400 Hz (just under the new
// Nyquist), Hamming-windowed, computed once at construction. After filtering,
// pick samples at the (fractional) integer decimation positions. A 63-tap
// FIR at 48 kHz costs ~3 M MACs/s — negligible inside a worklet.
const WORKLET_CODE = `
class DownsampleWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.ratio = sampleRate / this.targetRate;
    // Design a windowed-sinc lowpass. Cutoff at 7400 Hz leaves a small
    // transition band before Nyquist (8 kHz).
    const N = 63;                          // tap count (odd → linear phase, integer group delay)
    const fc = 7400 / sampleRate;          // normalized cutoff (fraction of input fs)
    const taps = new Float32Array(N);
    const M = (N - 1) / 2;
    let sum = 0;
    for (let n = 0; n < N; n++) {
      const k = n - M;
      const sinc = k === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * k) / (Math.PI * k);
      const win = 0.54 - 0.46 * Math.cos((2 * Math.PI * n) / (N - 1));  // Hamming
      taps[n] = sinc * win;
      sum += taps[n];
    }
    // Normalize for unity DC gain
    for (let n = 0; n < N; n++) taps[n] /= sum;
    this.taps = taps;
    this.N = N;
    // Delay line for FIR (circular not needed; linear works fine for short)
    this.delay = new Float32Array(N - 1);
    this.acc = 0;
    this.buf = [];
    this.flushSize = 1024; // ~64ms at 16 kHz
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;
    const ch0 = channels[0];
    const ch1 = channels[1];
    const length = ch0.length;
    const taps = this.taps;
    const N = this.N;
    const delay = this.delay;
    for (let i = 0; i < length; i++) {
      // Mix to mono
      let x = ch0[i];
      if (ch1) x = (x + ch1[i]) * 0.5;
      // FIR convolution with delay line. Compute y first using the existing
      // delay (oldest…newest, taps[0]…taps[N-2]) and the new sample x as
      // taps[N-1]; then shift delay so 'x' becomes the newest tap.
      let y = taps[N - 1] * x;
      for (let n = 0; n < N - 1; n++) y += taps[n] * delay[n];
      // Shift delay: drop oldest, append x
      for (let n = 0; n < N - 2; n++) delay[n] = delay[n + 1];
      delay[N - 2] = x;
      // Decimate: emit one output every \`ratio\` filtered samples
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        this.buf.push(y);
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
