/**
 * Audio-architecture probe (diagnostics only — NOT part of the capture brick).
 *
 * Installed at document_start in the MAIN world, BEFORE the page (Zoom/Teams)
 * creates any of its audio machinery, so it can wrap the constructors and see
 * the whole picture: where does the meeting's audio actually live?
 *
 *   - AudioContext      → count, state, sampleRate, and a destination TAP that
 *                         counts samples + peak amplitude (proves audio plays
 *                         through Web Audio, e.g. Zoom's WASM-decoded output).
 *   - RTCPeerConnection → getStats() polled for inbound-rtp audio (bytes/packets
 *                         /audioLevel) — proves remote audio over WebRTC even
 *                         when ontrack never fires.
 *   - getUserMedia / getDisplayMedia / tabCapture → call log + track kinds.
 *   - AudioWorklet.addModule → module URLs (WASM audio worklets).
 *   - WebAssembly.instantiate(Streaming) → count.
 *   - createMediaStreamSource / createMediaStreamDestination → counts.
 *
 * Everything lands on window.__vexaProbe; pageDiag() (inpage.ts) snapshots it
 * into the 5s diag that the background forwards to the recorder.
 */

export interface ProbeState {
  audioContexts: Array<{ state: string; sampleRate: number; destConnects: number; samples: number; peak: number }>;
  gum: Array<{ audio: boolean; video: boolean; tracks: string[] }>;
  gdm: Array<{ audio: boolean; video: boolean; tracks: string[] }>;
  worklets: string[];
  wasmInstantiations: number;
  msSources: number;
  msDestinations: number;
  scriptProcessors: number;
  audioWorkletNodes: number;
  pcAudioStats: Array<{ pc: number; state: string; inboundAudio: number; bytes: number; packets: number; level: number }>;
}

export function installAudioProbe(log: (m: string) => void = () => {}): void {
  const w = window as any;
  if (w.__vexaProbe) return;
  const P: ProbeState = w.__vexaProbe = {
    audioContexts: [], gum: [], gdm: [], worklets: [],
    wasmInstantiations: 0, msSources: 0, msDestinations: 0,
    scriptProcessors: 0, audioWorkletNodes: 0, pcAudioStats: [],
  };

  // ── AudioContext (READ-ONLY — never mutates the page's audio graph) ──────
  // We DO NOT tap the destination or patch AudioNode.connect: doing so risks
  // disrupting playback (the user reported audio loss). We only record that a
  // context exists, its sampleRate, and track its state non-destructively via
  // addEventListener (no onstatechange clobber). "samples"/"peak" stay 0 — they
  // are no longer measured; speech presence comes from getStats audioLevel and
  // the DOM active-speaker, not from touching the graph.
  const OrigAC = w.AudioContext || w.webkitAudioContext;
  if (OrigAC) {
    const Wrapped: any = function (this: any, ...a: any[]) {
      const c: AudioContext = new OrigAC(...a);
      const rec = { state: c.state, sampleRate: c.sampleRate, destConnects: 0, samples: 0, peak: 0 };
      P.audioContexts.push(rec);
      try { c.addEventListener('statechange', () => { rec.state = c.state; }); } catch { /* ignore */ }
      log(`[probe] AudioContext #${P.audioContexts.length} sr=${c.sampleRate} state=${c.state} (read-only)`);
      return c;
    };
    Wrapped.prototype = OrigAC.prototype;
    Object.setPrototypeOf(Wrapped, OrigAC);
    w.AudioContext = Wrapped; w.webkitAudioContext = Wrapped;

    // Count graph-builders (wrappers return the REAL node — no behavior change).
    const ACP: any = OrigAC.prototype;
    const wrapCount = (name: string, key: keyof ProbeState) => {
      const orig = ACP[name]; if (typeof orig !== 'function') return;
      ACP[name] = function (...args: any[]) { (P as any)[key]++; return orig.apply(this, args); };
    };
    wrapCount('createMediaStreamSource', 'msSources');
    wrapCount('createMediaStreamDestination', 'msDestinations');
    wrapCount('createScriptProcessor', 'scriptProcessors');
  }

  if (typeof (w.AudioWorkletNode) === 'function') {
    const OrigAWN = w.AudioWorkletNode;
    w.AudioWorkletNode = function (this: any, ...a: any[]) { P.audioWorkletNodes++; return new OrigAWN(...a); };
    w.AudioWorkletNode.prototype = OrigAWN.prototype;
  }

  // ── getUserMedia / getDisplayMedia ──────────────────────────────────────
  const md = navigator.mediaDevices as any;
  const wrapGet = (name: string, bucket: 'gum' | 'gdm') => {
    if (!md || typeof md[name] !== 'function') return;
    const orig = md[name].bind(md);
    md[name] = async (constraints: any) => {
      const stream: MediaStream = await orig(constraints);
      try {
        P[bucket].push({
          audio: !!constraints?.audio, video: !!constraints?.video,
          tracks: stream.getTracks().map(t => `${t.kind}:${(t.label || '').slice(0, 30)}`),
        });
        log(`[probe] ${name} → ${stream.getTracks().map(t => t.kind).join(',')}`);
      } catch { /* ignore */ }
      return stream;
    };
  };
  wrapGet('getUserMedia', 'gum');
  wrapGet('getDisplayMedia', 'gdm');

  // ── AudioWorklet.addModule (worklet URLs) ───────────────────────────────
  try {
    const awProto = (w.AudioWorklet && w.AudioWorklet.prototype) || null;
    if (awProto && typeof awProto.addModule === 'function') {
      const orig = awProto.addModule;
      awProto.addModule = function (url: string, ...rest: any[]) { try { P.worklets.push(String(url).slice(0, 80)); } catch {} return orig.call(this, url, ...rest); };
    }
  } catch { /* ignore */ }

  // ── WebAssembly.instantiate(Streaming) count ────────────────────────────
  try {
    for (const k of ['instantiate', 'instantiateStreaming'] as const) {
      const orig = (WebAssembly as any)[k];
      if (typeof orig === 'function') (WebAssembly as any)[k] = function (...a: any[]) { P.wasmInstantiations++; return orig.apply(this, a); };
    }
  } catch { /* ignore */ }

  // ── WebRTC inbound-audio getStats polling ───────────────────────────────
  setInterval(() => {
    const pcs: RTCPeerConnection[] = w.__vexa_peer_connections || [];
    Promise.all(pcs.slice(0, 16).map(async (pc, i) => {
      try {
        const stats = await pc.getStats();
        let inboundAudio = 0, bytes = 0, packets = 0, level = 0;
        stats.forEach((r: any) => {
          if (r.type === 'inbound-rtp' && r.kind === 'audio') { inboundAudio++; bytes += r.bytesReceived || 0; packets += r.packetsReceived || 0; level = Math.max(level, r.audioLevel || 0); }
        });
        return { pc: i, state: pc.connectionState, inboundAudio, bytes, packets, level };
      } catch { return null; }
    })).then(rows => { P.pcAudioStats = rows.filter(Boolean) as any[]; }).catch(() => {});
  }, 2000);

  log('[probe] installed (AudioContext, getUserMedia, getStats, worklet, wasm hooks live)');
}
