import { Page } from 'playwright';
import { log } from '../../../utils';

/**
 * Rich observation harness for Zoom Web architecture research.
 *
 * Gated by env var ZOOM_OBSERVE=true. Runs in parallel with normal
 * transcription. Dumps per-tick:
 *   - Audio elements: count, paused, srcObject identity, real-time
 *     audio level via AnalyserNode RMS
 *   - WebRTC receivers: SSRC + audioLevel + bytesReceived per receiver
 *     (correlates SSRC ↔ "what is the audio source")
 *   - WebSocket activity: monkey-patches WebSocket constructor at init
 *     to capture URL + recent message kinds
 *   - DOM speaker badge: active speaker name + all visible tile names
 *   - Caption presence: any rendered caption-like text + CC button state
 *
 * Output goes to bot stdout via console.log → [BotConsole] channel
 * with prefix `[Vexa] [ZOOM_OBSERVE]` for easy grep.
 */
export async function startZoomRichObservation(page: Page): Promise<void> {
  log('[Vexa] [ZOOM_OBSERVE] Installing rich observation harness in page context...');

  await page.evaluate(() => {
    const w = window as any;
    if (w.__vexaZoomObserve) return; // idempotent
    w.__vexaZoomObserve = { startedAt: Date.now() };

    // --- WebSocket monkey-patch (captures all WS connections) -------------
    const origWS = w.WebSocket;
    const wsLog: any[] = [];
    w.__vexaWsLog = wsLog;
    w.WebSocket = function (this: any, url: any, protocols?: any) {
      const u = String(url);
      const ws = new origWS(url, protocols);
      const meta: any = {
        url: u.substring(0, 200),
        opened_at: Date.now(),
        protocols: protocols ? String(protocols).substring(0, 100) : null,
        msgs_in: 0,
        msgs_out: 0,
        sample_in: [] as string[],
        sample_out: [] as string[],
        readyState: 0,
      };
      wsLog.push(meta);
      ws.addEventListener('open', () => { meta.readyState = 1; });
      ws.addEventListener('close', () => { meta.readyState = 3; });
      ws.addEventListener('message', (ev: any) => {
        meta.msgs_in++;
        if (meta.sample_in.length < 5) {
          let sample: string;
          if (typeof ev.data === 'string') sample = ev.data.substring(0, 250);
          else if (ev.data instanceof ArrayBuffer) sample = `<ArrayBuffer ${ev.data.byteLength}b>`;
          else if (ev.data instanceof Blob) sample = `<Blob ${ev.data.size}b ${ev.data.type}>`;
          else sample = `<${typeof ev.data}>`;
          meta.sample_in.push(sample);
        }
      });
      const origSend = ws.send.bind(ws);
      ws.send = function (data: any) {
        meta.msgs_out++;
        if (meta.sample_out.length < 5) {
          let sample: string;
          if (typeof data === 'string') sample = data.substring(0, 250);
          else if (data instanceof ArrayBuffer) sample = `<ArrayBuffer ${data.byteLength}b>`;
          else sample = `<${typeof data}>`;
          meta.sample_out.push(sample);
        }
        return origSend(data);
      };
      return ws;
    } as any;
    Object.assign(w.WebSocket, origWS);
    w.WebSocket.prototype = origWS.prototype;

    // --- Audio level analysers (one per audio element with srcObject) -----
    const analysers = new Map<HTMLMediaElement, { node: AnalyserNode, ctx: AudioContext, buffer: Float32Array }>();
    function ensureAnalyser(el: HTMLMediaElement) {
      if (analysers.has(el)) return analysers.get(el)!;
      const stream = (el as any).srcObject as MediaStream;
      if (!stream || stream.getAudioTracks().length === 0) return null;
      try {
        const ctx = new AudioContext();
        const src = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        src.connect(analyser);
        const buffer = new Float32Array(analyser.fftSize);
        const entry = { node: analyser, ctx, buffer };
        analysers.set(el, entry);
        return entry;
      } catch (e) {
        return null;
      }
    }
    function getRMS(entry: { node: AnalyserNode, buffer: Float32Array }): number {
      // v0.10.5.3 fix: TypeScript 5.7+ tightened Float32Array generic to
      // distinguish ArrayBuffer from SharedArrayBuffer. `new Float32Array(N)`
      // returns Float32Array<ArrayBufferLike>; getFloatTimeDomainData expects
      // Float32Array<ArrayBuffer>. They're identical at runtime — cast is
      // safe because buffer was created with `new Float32Array(fftSize)`.
      entry.node.getFloatTimeDomainData(entry.buffer as Float32Array<ArrayBuffer>);
      let sum = 0;
      for (let i = 0; i < entry.buffer.length; i++) sum += entry.buffer[i] * entry.buffer[i];
      return Math.sqrt(sum / entry.buffer.length);
    }

    // --- Periodic dump ---------------------------------------------------
    let tick = 0;
    setInterval(async () => {
      tick++;

      // Audio elements + RMS
      const els = Array.from(document.querySelectorAll('audio, video')) as HTMLMediaElement[];
      const audioRows: any[] = [];
      for (let i = 0; i < els.length; i++) {
        const el = els[i];
        const stream = (el as any).srcObject;
        if (!(stream instanceof MediaStream)) continue;
        const tracks = stream.getAudioTracks();
        if (tracks.length === 0) continue;
        const entry = ensureAnalyser(el);
        const rms = entry ? getRMS(entry) : -1;
        audioRows.push({
          idx: i,
          tag: el.tagName,
          paused: el.paused,
          stream_id: stream.id.substring(0, 12),
          track_id: tracks[0].id.substring(0, 12),
          track_label: tracks[0].label,
          track_muted: tracks[0].muted,
          track_readyState: tracks[0].readyState,
          rms: Number(rms.toFixed(4)),
        });
      }

      // WebRTC receivers + stats
      const pcs = (w.__vexa_peer_connections || []) as RTCPeerConnection[];
      const wrtcRows: any[] = [];
      for (let p = 0; p < pcs.length; p++) {
        try {
          const pc = pcs[p];
          const recs = pc.getReceivers();
          for (let r = 0; r < recs.length; r++) {
            const rec = recs[r];
            const t = rec.track;
            if (!t || t.kind !== 'audio') continue;
            try {
              const stats = await rec.getStats();
              let inboundRtp: any = null;
              stats.forEach((s: any) => {
                if (s.type === 'inbound-rtp' && s.kind === 'audio') inboundRtp = s;
              });
              if (inboundRtp) {
                wrtcRows.push({
                  pc: p,
                  rec: r,
                  track_id: t.id.substring(0, 12),
                  ssrc: inboundRtp.ssrc,
                  audioLevel: inboundRtp.audioLevel,
                  totalAudioEnergy: Number((inboundRtp.totalAudioEnergy || 0).toFixed(2)),
                  bytesReceived: inboundRtp.bytesReceived,
                  packetsReceived: inboundRtp.packetsReceived,
                });
              }
            } catch (e: any) {
              wrtcRows.push({ pc: p, rec: r, err: e.message });
            }
          }
        } catch (e: any) { wrtcRows.push({ pc: p, err: e.message }); }
      }

      // DOM badge state
      const activeNameEl = document.querySelector('.speaker-active-container__video-frame .video-avatar__avatar-footer span');
      const allTileNames = Array.from(document.querySelectorAll('.video-avatar__avatar-footer span')).map(s => s.textContent?.trim()).filter(Boolean);

      // Caption presence
      const ccBtn = document.querySelector('button[aria-label*="caption" i], button[aria-label*="CC" i]') as HTMLElement | null;
      const ccElements = Array.from(document.querySelectorAll('.lt-meeting-caption, .caption-rendered, [class*="live-transcript"]')).map(el => ({
        sel: (el as HTMLElement).className,
        text: el.textContent?.trim().substring(0, 80)
      }));

      // WebSocket status
      const wsRows = wsLog.map(meta => ({
        url: meta.url,
        readyState: meta.readyState,
        msgs_in: meta.msgs_in,
        msgs_out: meta.msgs_out,
      }));

      // Emit one big snapshot per tick
      console.log(`[Vexa] [ZOOM_OBSERVE] tick=${tick} ` + JSON.stringify({
        ts: Date.now(),
        audio: audioRows,
        wrtc: wrtcRows,
        dom: {
          active: activeNameEl?.textContent?.trim() || null,
          tiles: allTileNames,
        },
        cc: {
          btn_present: !!ccBtn,
          btn_aria: ccBtn?.getAttribute('aria-label'),
          elements: ccElements,
        },
        ws: wsRows,
      }));

      // Every 5 ticks (~10s), also dump WebSocket samples (heavier)
      if (tick % 5 === 0) {
        const wsSamples = wsLog.map(meta => ({
          url: meta.url.substring(0, 80),
          msgs_in: meta.msgs_in,
          sample_in: meta.sample_in,
          sample_out: meta.sample_out,
        }));
        console.log(`[Vexa] [ZOOM_OBSERVE_WS] tick=${tick} ` + JSON.stringify(wsSamples));
      }
    }, 2000);

    console.log('[Vexa] [ZOOM_OBSERVE] Harness installed. Dumping every 2s.');
  });

  log('[Vexa] [ZOOM_OBSERVE] Harness installed in page context.');
}
