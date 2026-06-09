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

  // ── Speaker identity (Google Meet) ─────────────────────────────────────
  // Port of the bot's speaker-identity vote/lock: when audio arrives on track
  // N while exactly one participant's speaking indicator is lit, vote
  // "track N = that name"; lock after 2 consistent votes (≥70% of votes).
  // Locked names are pushed upstream so transcripts show real names instead
  // of "Speaker N". Selectors mirror vexa-bot googlemeet/selectors.ts.
  const GM_PARTICIPANT_SELECTORS = ['div[data-participant-id]', '[data-participant-id]'];
  const GM_SPEAKING_CLASSES = ['Oaajhc', 'HX2H7', 'wEsLMd', 'OgVli', 'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator'];
  const LOCK_THRESHOLD = 2;
  const LOCK_RATIO = 0.7;
  const trackVotes = new Map<number, Map<string, number>>();
  const lockedNames = new Map<number, string>();
  const announcedNames = new Map<number, string>();
  const trackLastAudio = new Map<number, number>();

  function gmTileName(el: HTMLElement): string | null {
    const nt = el.querySelector('span.notranslate') as HTMLElement | null;
    const t = nt?.textContent?.trim();
    if (t && t.length > 1 && t.length < 50) return t;
    return null;
  }

  function gmSpeakingNames(): string[] {
    if (!location.hostname.endsWith('meet.google.com')) return [];
    const selfName = (document.querySelector('[data-self-name]') as HTMLElement | null)?.getAttribute('data-self-name') || '';
    const speaking: string[] = [];
    const seen = new Set<string>();
    for (const sel of GM_PARTICIPANT_SELECTORS) {
      document.querySelectorAll(sel).forEach(el => {
        const elh = el as HTMLElement;
        const id = elh.getAttribute('data-participant-id') || '';
        if (!id || seen.has(id)) return;
        seen.add(id);
        const name = gmTileName(elh);
        if (!name || name === selfName) return; // self is the mic stream ("You"), never a remote track
        const lit = GM_SPEAKING_CLASSES.some(c => elh.classList.contains(c) || !!elh.querySelector('.' + c));
        if (lit) speaking.push(name);
      });
    }
    return [...new Set(speaking)];
  }

  function nameTaken(name: string, except: number): boolean {
    for (const [i, n] of lockedNames) if (i !== except && n === name) return true;
    return false;
  }

  function vote(index: number, name: string, weight: number): void {
    if (lockedNames.has(index) || nameTaken(name, index)) return;
    let v = trackVotes.get(index);
    if (!v) { v = new Map(); trackVotes.set(index, v); }
    v.set(name, (v.get(name) || 0) + weight);
    const total = [...v.values()].reduce((a, b) => a + b, 0);
    const top = [...v.entries()].sort((a, b) => b[1] - a[1])[0];
    if (top[1] >= LOCK_THRESHOLD && top[1] / total >= LOCK_RATIO && !nameTaken(top[0], index)) {
      lockedNames.set(index, top[0]);
      console.log(`${TAG} locked track ${index} = "${top[0]}"`);
    }
  }

  setInterval(() => {
    if (!running) return;
    const now = Date.now();
    const speaking = gmSpeakingNames();
    if (speaking.length >= 1 && speaking.length <= 2) {
      for (const [index, last] of trackLastAudio) {
        if (index >= MIC_INDEX) continue;        // mic is always "You"
        if (now - last > 700) continue;           // only tracks with audio right now
        if (lockedNames.has(index)) continue;
        if (speaking.length === 1) vote(index, speaking[0], 1.0);
        else for (const n of speaking) vote(index, n, 0.5);
      }
    }
    // Push newly locked (or top-voted) names upstream
    const updates: Record<string, string> = {};
    for (const [index, name] of lockedNames) {
      if (announcedNames.get(index) !== name) { updates[String(index)] = name; announcedNames.set(index, name); }
    }
    if (Object.keys(updates).length > 0) post('speakers', { speakers: updates });
  }, 500);

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
          trackLastAudio.set(index, Date.now());
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
