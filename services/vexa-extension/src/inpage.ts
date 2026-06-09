/**
 * In-page capture (MAIN world).
 *
 * This is the bot's browser-side per-speaker capture loop — the exact live
 * Google Meet path from vexa-bot/core/src/index.ts (the page.evaluate block
 * that wires each participant's <audio>/<video> MediaStream into a
 * ScriptProcessor and emits resampled 16 kHz PCM). The only change: where the
 * bot called window.__vexaPerSpeakerAudioData(index, data) across the
 * Playwright bridge, we postMessage the chunk to the content script, which
 * relays it to the background service worker's WebSocket.
 *
 * It must run in the MAIN world (not the isolated content-script world) so it
 * can read the page-assigned el.srcObject MediaStream objects directly.
 */

(() => {
  const TAG = '[vexa-inpage]';
  const TARGET_SAMPLE_RATE = 16000;
  const BUFFER_SIZE = 4096;

  let running = false;
  let rescanInterval: number | null = null;
  const connectedStreamIds = new Set<string>();
  const contexts: AudioContext[] = [];
  let nextStreamIndex = 0;

  // Reserved high index for the local microphone ("You"), kept clear of the
  // 0-based participant element indices.
  const MIC_INDEX = 1000;
  let micStream: MediaStream | null = null;

  function post(type: string, payload: any) {
    window.postMessage({ __vexa: true, type, ...payload }, '*');
  }

  function streamCount(): number {
    return connectedStreamIds.size + (micStream ? 1 : 0);
  }

  /**
   * Capture the LOCAL microphone (your own voice). Remote participants live in
   * page media elements; your own voice does not, so we grab it directly. The
   * Meet origin already holds mic permission, so this won't re-prompt.
   */
  async function startMic(): Promise<void> {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      const source = ctx.createMediaStreamSource(micStream);
      const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);
      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!running) return;
        const data = e.inputBuffer.getChannelData(0);
        let maxVal = 0;
        for (let i = 0; i < data.length; i++) {
          const a = Math.abs(data[i]);
          if (a > maxVal) maxVal = a;
        }
        if (maxVal > 0.005) post('audio', { index: MIC_INDEX, pcm: Array.from(data) });
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      contexts.push(ctx);
      post('speakers', { speakers: { [MIC_INDEX]: 'You' } });
      console.log(`${TAG} microphone capture started ("You")`);
    } catch (err: any) {
      console.log(`${TAG} mic capture unavailable: ${err.message}`);
      micStream = null;
    }
  }

  function findMediaElements(): HTMLMediaElement[] {
    return Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
      !el.paused &&
      el.srcObject instanceof MediaStream &&
      el.srcObject.getAudioTracks().length > 0
    ) as HTMLMediaElement[];
  }

  function connectElement(el: HTMLMediaElement, index: number): boolean {
    try {
      const stream: MediaStream = (el as any).srcObject;
      if (!stream || stream.getAudioTracks().length === 0) return false;
      const streamId = stream.id;
      if (connectedStreamIds.has(streamId)) return false;

      const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);

      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!running) return;
        const data = e.inputBuffer.getChannelData(0);
        // Only send when there is actual audio (silence gate) — mirrors the bot.
        let maxVal = 0;
        for (let i = 0; i < data.length; i++) {
          const a = Math.abs(data[i]);
          if (a > maxVal) maxVal = a;
        }
        if (maxVal > 0.005) {
          post('audio', { index, pcm: Array.from(data) });
        }
      };

      source.connect(processor);
      processor.connect(ctx.destination);
      connectedStreamIds.add(streamId);
      contexts.push(ctx);

      const track = stream.getAudioTracks()[0];
      track.addEventListener('ended', () => {
        connectedStreamIds.delete(streamId);
      });

      console.log(`${TAG} stream ${index} connected (track ${track.id.substring(0, 8)})`);
      return true;
    } catch (err: any) {
      console.log(`${TAG} stream ${index} error: ${err.message}`);
      return false;
    }
  }

  async function start() {
    if (running) return;
    running = true;
    console.log(`${TAG} starting capture`);

    // Your own voice first — it doesn't depend on other participants being present.
    await startMic();
    post('capture-started', { streams: streamCount() });

    let mediaElements: HTMLMediaElement[] = [];
    for (let attempt = 0; attempt < 10 && running; attempt++) {
      mediaElements = findMediaElements();
      if (mediaElements.length > 0) break;
      await new Promise(r => setTimeout(r, 2000));
    }
    if (!running) return;

    for (let i = 0; i < mediaElements.length; i++) {
      if (connectElement(mediaElements[i], i)) nextStreamIndex = i + 1;
    }
    nextStreamIndex = Math.max(nextStreamIndex, mediaElements.length);

    // Periodic re-scan: late joiners / element recycling.
    rescanInterval = window.setInterval(() => {
      if (!running) return;
      for (const el of findMediaElements()) {
        const stream: MediaStream = (el as any).srcObject;
        if (stream && !connectedStreamIds.has(stream.id)) {
          if (connectElement(el, nextStreamIndex)) nextStreamIndex++;
        }
      }
    }, 15000);

    post('capture-started', { streams: streamCount() });
    console.log(`${TAG} capture started with ${streamCount()} stream(s) (mic + participants)`);
  }

  function stop() {
    if (!running) return;
    running = false;
    if (rescanInterval !== null) { clearInterval(rescanInterval); rescanInterval = null; }
    if (micStream) { for (const t of micStream.getTracks()) { try { t.stop(); } catch { /* ignore */ } } micStream = null; }
    for (const ctx of contexts) { try { ctx.close(); } catch { /* ignore */ } }
    contexts.length = 0;
    connectedStreamIds.clear();
    nextStreamIndex = 0;
    console.log(`${TAG} capture stopped`);
    post('capture-stopped', {});
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.__vexaControl !== true) return;
    if (data.command === 'vexa-start') start();
    else if (data.command === 'vexa-stop') stop();
  });

  post('inpage-ready', {});
  console.log(`${TAG} loaded`);
})();
