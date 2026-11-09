/**
 * createGmeetPageCapture: Google Meet's PageCapture (@vexa/capture-codec), built on the existing v1 producer
 * (createGmeetCaptureV1: one channel per participant media element, each chunk stamped with the name of the
 * one lit tile at that moment, or none when zero or two are lit). Nothing new is captured here; this only
 * maps the producer's sink onto the contract, so the bot and the capture extension run the same Meet code.
 * Meet exposes its participants' audio as media elements: no WebRTC hook is needed (or wanted: its mirrors
 * would be found as extra elements and every voice captured twice).
 */
import type { PageCapture, PageCaptureOptions, AudioChunk } from '@vexa/capture-codec';
import { createGmeetCaptureV1 } from './gmeet-capture-v1.js';
import type { GmeetCaptureV1, GmeetCaptureV1Options } from './gmeet-capture-v1.js';

export function createGmeetPageCapture(
  opts: PageCaptureOptions & { makeV1?: (o: GmeetCaptureV1Options) => GmeetCaptureV1 },
): PageCapture {
  let stopped = false;
  const log = opts.log ?? (() => { /* silent */ });
  const v1 = (opts.makeV1 ?? createGmeetCaptureV1)({
    selfName: opts.selfName,
    log,
    sink: {
      audioChunk: (c: AudioChunk) => {
        if (stopped) return;
        opts.onAudio(c.speakerIndex, c.speakerName || null, c.samples, c.ts);
      },
      event: (ev: any) => {                  // active-speaker edges: teed to the host (the chunks already carry the binding)
        if (stopped || !ev || ev.kind !== 'active-speaker' || typeof ev.speaker !== 'string') return;
        opts.onSpeakerHint?.(ev.speaker, typeof ev.ts === 'number' ? ev.ts : Date.now(), !!(ev.detail && ev.detail.isEnd));
      },
      finalize: () => { /* nothing buffered */ },
    },
  });
  v1.start().then(
    () => opts.observe?.('pagecapture', { type: 'pagecapture-started', platform: 'google_meet', channels: v1.streamCount() }, Date.now()),
    (e: unknown) => opts.observe?.('pagecapture', { type: 'pagecapture-failed', platform: 'google_meet', error: String(e) }, Date.now()),
  );
  return {
    channels: () => v1.streamCount(),
    contextState: () => 'unknown',            // one context per participant element; each logs its own state
    stop(): void {
      if (stopped) return;
      stopped = true;
      try { v1.stop(); } catch { /* already gone */ }
    },
  };
}
