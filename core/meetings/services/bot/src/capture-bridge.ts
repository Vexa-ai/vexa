/**
 * Capture bridge (2b) — the browser-resident capture → pipeline pump + the speak path.
 *
 * ╔══════════════════════════════════════════════════════════════════════════════════════╗
 * ║ L4 (O6/VM): live-validated against a real meeting.                                      ║
 * ║ This whole file is BROWSER-RESIDENT glue: it injects page-side capture, bridges PCM     ║
 * ║ frames over the Playwright boundary, and drives the meeting-UI mic for speaking. None   ║
 * ║ of it can be proven by a unit test (no DOM, no MediaRecorder, no PulseAudio in CI) — it ║
 * ║ is code-complete + build-clean, and PROVEN only by the O6 VM run. The offline-provable  ║
 * ║ engine it pumps into is pipeline.ts (L2/L3).                                            ║
 * ╚══════════════════════════════════════════════════════════════════════════════════════╝
 *
 * Ported faithfully from the working production bot
 *   services/vexa-bot/core/src/index.ts:
 *     • launch (authenticated, persistent context + S3 restore)  → index.ts:2313–2347
 *     • the per-speaker bridge binding + page-side capture wiring → index.ts:1930, 1947–1957
 *     • the Node-side frame callback shape (speakerIndex, number[]) → index.ts:1598–1605
 *     • the speak path (Redis act → meeting-UI mic unmute → PulseAudio tts_sink) → index.ts:595, 1039–1059
 *
 * Isolation note: the page-side capture module (@vexa/gmeet-capture / @vexa/capture-codec) is
 * NOT a bot dependency (gate:isolation) — it is a BROWSER bundle loaded into the page at runtime
 * (production's `window.VexaBrowserUtils`, installed via addInitScript of the prebuilt
 * browser-utils.global.js). The Node side here imports nothing from those packages; PCM frames
 * cross as plain `(speakerIndex: number, samples: number[])` over `page.exposeFunction`, exactly
 * as production does, so the bot's import surface stays within the gate.
 */
import {
  launchPersistentBrowser,
  syncBrowserDataFromS3,
  syncBrowserDataToS3,
  cleanStaleLocks,
  getAuthenticatedBrowserArgs,
  makeEphemeralProfileDir,
  removeProfileDir,
  type Page,
  type BrowserContext,
} from '@vexa/remote-browser';
import { getJoinBrowserArgs, resolveConferenceFrame } from '@vexa/join';
import type { RecordingMasterFormat } from '@vexa/recording';
import { isMixedLanePlatform, type Invocation } from './config.js';
import type { BotPipeline } from './pipeline.js';
import type { BotRecordingSink } from './recording.js';
import type { TelemetrySink } from './ports.js';
import type { RemoteAudioActivityTap } from './aloneness.js';
import { createTtsPlayback } from './tts-playback.js';

/** Float32 PCM → base64 of its little-endian bytes — the EXACT codec wire payload, so a stored
 *  captured-signal.v1 frame round-trips through @vexa/capture-codec (encode→decode→same PCM). */
export function pcmToBase64(pcm: Float32Array): string {
  return Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength).toString('base64');
}
/** Cheap level read for a captured frame (and the no-signal/silence oracle later). */
export function rmsOf(pcm: Float32Array): number {
  if (!pcm.length) return 0;
  let s = 0;
  for (let i = 0; i < pcm.length; i++) s += pcm[i] * pcm[i];
  return Math.sqrt(s / pcm.length);
}

/** The activity observer sits only on REMOTE browser capture callbacks. The local speak/TTS
 * path never calls it, so bot speech cannot extend the meeting's silence window. */
export function makeRemoteAudioEnergyTap(activity?: RemoteAudioActivityTap) {
  return (pcm: Float32Array): void => activity?.observeRemoteEnergy(rmsOf(pcm));
}

/**
 * Build the O-TEL-1 raw-signal tap — the EXACT closure the capture bridge tees each frame into,
 * factored out so it is offline-provable WITHOUT a Playwright page (telemetry.test.ts drives this
 * directly). When `telemetry` is unset the returned tap is a single truthiness check — zero
 * overhead, the proven O6 capture path is byte-for-byte unchanged. captureFrame is fire-and-forget;
 * a tap throw is swallowed so it can NEVER reach the pipeline.
 */
export function makeTelemetryTap(lane: 'gmeet' | 'mixed', telemetry?: TelemetrySink) {
  let seq = 0;
  return (speakerIndex: number, pcm: Float32Array, ts: number, speakerName?: string, hint?: string): void => {
    if (!telemetry) return;   // unset ⇒ one branch, nothing computed (never alter the capture path)
    try {
      telemetry.captureFrame({ seq: seq++, ts, speakerIndex, speakerName, hint, pcm: pcmToBase64(pcm), pcm_len: pcm.length, rms: rmsOf(pcm), lane });
    } catch { /* telemetry must not break capture */ }
  };
}

/**
 * Build the mixed-lane speaker-hint sink — the EXACT closure the bridge exposes as
 * `__vexaSpeakerHint`, factored out so it is offline-provable WITHOUT a Playwright page.
 *
 * CLOCK CONTRACT: hint tMs and audio tsMs entering the pipeline share ONE domain —
 * epoch ms. The page-side watchers stamp Date.now() (epoch), so normally the value
 * passes through untouched; a page that emits a non-epoch time (e.g. a relative
 * performance.now()) would make every hint window miss every speech turn, so an
 * implausible skew is re-stamped Node-side and warned LOUDLY, never silently bound
 * to nothing. Also counts arrivals (C1 hop 2: page → Node).
 */
export const HINT_MAX_SKEW_MS = 10 * 60 * 1000;
export function makeSpeakerHintSink(
  pipeline: Pick<BotPipeline, 'recordHint'>,
  warn: (m: string) => void = (m) => console.warn(m),
  /** O-TEL-1: the same sink the audio tap feeds. Mixed-lane hints arrive HERE, not on the audio
   *  frames, so a session recorded without this tee stores audio that can never reproduce
   *  attribution offline. Teed with the post-guard `t`, so the stored hint carries the clock the
   *  pipeline actually saw. */
  telemetry?: TelemetrySink,
): { sink: (name: string, tMs?: number, isEnd?: boolean) => void; crossed: () => number } {
  let crossed = 0;
  return {
    crossed: () => crossed,
    sink: (name: string, tMs?: number, isEnd?: boolean): void => {
      crossed++;
      let t = tMs ?? Date.now();
      const skew = Math.abs(t - Date.now());
      if (skew > HINT_MAX_SKEW_MS) {
        warn(`[bot] hint-clock-skew: hint tMs=${t} is ${Math.round(skew / 1000)}s off the epoch audio clock — page emitted a non-epoch timestamp; re-stamping (name=${name})`);
        t = Date.now();
      }
      if (telemetry?.captureHint) {
        try { telemetry.captureHint({ type: 'hint', t, name, isEnd, lane: 'mixed' }); }
        catch { /* telemetry must not break capture */ }
      }
      pipeline.recordHint(name, t, isEnd);
    },
  };
}

/**
 * One CSRC transition as it crosses the capture bridge — the transport sensor's captured-signal
 * record.
 *
 * The mixed lane's audio is a single server-side mix, so nothing in the waveform says who is
 * speaking; the RTP layer, however, labels that mix with the sources it mixed. A transition is a
 * turn edge OBSERVED rather than inferred: `csrc` is the transport's own per-stream-stable id for
 * a source (a number, not a name — this record names nobody) and `active` is the edge's direction.
 *
 * Like `TeamsCaptionRecord` it is deliberately NOT a `HintEvent`: a hint feeds the name binder, and
 * an observation that silently became one would change attribution behaviour under cover of a
 * diagnostic. In this iteration NOTHING reads this record. `t` shares the audio frames' epoch-ms
 * clock — the only reason a stored tape can line these edges up against the audio at all.
 */
export interface CsrcRecord {
  type: 'csrc';
  t: number;
  /** The RTP contributing-source identifier (an unsigned 32-bit number, stable per stream). */
  csrc: number;
  active: boolean;
  /** 0..1 where the UA supplies it. Carried for offline analysis; never interpreted here. */
  audioLevel?: number;
  /** The media clock of the contribution, for offline alignment against the audio. */
  rtpTimestamp?: number;
  lane: 'mixed';
}

/** A telemetry sink that can ALSO store CSRC transitions. Structural + OPTIONAL for the same
 *  reason as the caption sink below: a recorder without the method degrades to log-only rather
 *  than to a throw inside the capture path. */
export type CsrcCapableSink = TelemetrySink & { captureCsrc?: (rec: CsrcRecord) => void };

/**
 * One typed observation from the capture path, as it crosses the bridge.
 *
 * Every producer here already emits typed observations — `signal-absent`, `indicator-silent`,
 * `captions-absent`, `main-audio-absent`, `csrc-poll-error` — and until now every one of them was
 * ONLY a log line, in a pod that is deleted minutes later. So a stored fixture could reproduce
 * what the bot HEARD but never what it NOTICED, and the question a fixture is usually asked —
 * "was the signal missing, or did we mis-read a signal that was there?" — had no answer inside
 * the fixture at all. They are data now. The log lines are unchanged: this is additive.
 *
 * `observation` is the producer's own payload, carried VERBATIM. The bridge adds only arrival
 * metadata (when, from which producer, in which lane) and interprets nothing — a bridge that
 * normalized these would be deciding, at capture time, what a later analysis is allowed to see.
 */
export interface ObservationRecord {
  type: 'observation';
  /** Epoch ms — the same clock the frames, hints, captions and transitions carry. */
  t: number;
  /** Which producer emitted it: 'teams-speakers' · 'teams-captions' · 'mixed' · 'csrc' · 'bot'. */
  source: string;
  lane: 'gmeet' | 'mixed';
  observation: Record<string, unknown>;
}

/** A telemetry sink that can ALSO store observations. Structural + OPTIONAL, as above. */
export type ObservationCapableSink = TelemetrySink & { captureObservation?: (rec: ObservationRecord) => void };

/**
 * Build the observation sink — the EXACT closure the bridge exposes as `__vexaObservation`,
 * factored out like its siblings so it is offline-provable WITHOUT a Playwright page.
 *
 * A payload that is not an object (a page handing over a string, or nothing at all) is wrapped
 * rather than dropped: a malformed observation still says that something happened, and dropping
 * it would make the sidecar quietly disagree with the log. Clock guard as everywhere else.
 */
export function makeObservationSink(
  lane: 'gmeet' | 'mixed',
  telemetry?: ObservationCapableSink,
  warn: (m: string) => void = (m) => console.warn(m),
  /** The ONE observation the lane consumes rather than merely stores: a roster display name.
   *  Everything else here stays a diagnostic. Called with the re-stamped time, after storage, for
   *  the same reason the transport edges are — a fixture that lacks what the run acted on cannot
   *  reproduce the run. */
  consumeRosterName?: (name: string, tMs: number) => void,
  /** …and the producer's account of how much of the roster it could read. */
  consumeRosterCoverage?: (named: number, participants: number, tMs: number) => void,
): { sink: (source: string, obs: unknown, tMs?: number) => void; crossed: () => number; stored: () => number } {
  let crossed = 0;
  let stored = 0;
  return {
    crossed: () => crossed,
    stored: () => stored,
    sink: (source: string, obs: unknown, tMs?: number): void => {
      crossed++;
      let t = tMs ?? Date.now();
      const skew = Math.abs(t - Date.now());
      if (skew > HINT_MAX_SKEW_MS) {
        warn(`[bot] observation-clock-skew: tMs=${t} is ${Math.round(skew / 1000)}s off the epoch audio clock — page emitted a non-epoch timestamp; re-stamping (source=${source})`);
        t = Date.now();
      }
      const payload: Record<string, unknown> = obs && typeof obs === 'object'
        ? obs as Record<string, unknown>
        : { note: String(obs) };
      if (typeof telemetry?.captureObservation === 'function') {
        try { telemetry.captureObservation({ type: 'observation', t, source, lane, observation: payload }); stored++; }
        catch { /* telemetry must not break capture */ }
      }
      // A roster name is WHO IS IN THE ROOM. It is not a hint and carries no time of speech, so it
      // can never attribute a turn on its own — it supplies canonical casing and it is what lets the
      // namer conclude, when one track and one name are all that remain, that they are each other.
      if (payload.type === 'roster-name' && typeof payload.name === 'string' && payload.name) {
        try { consumeRosterName?.(payload.name, t); } catch { /* never breaks capture */ }
      }
      if (payload.type === 'roster-coverage'
        && typeof payload.named === 'number' && typeof payload.participants === 'number') {
        try { consumeRosterCoverage?.(payload.named, payload.participants, t); } catch { /* never breaks capture */ }
      }
    },
  };
}

/**
 * Build the mixed-lane CSRC sink — the EXACT closure the bridge exposes as `__vexaCsrc`, factored
 * out like makeSpeakerHintSink / makeTeamsCaptionSink so it is offline-provable WITHOUT a
 * Playwright page.
 *
 * CLOCK CONTRACT is the hint sink's, for the same reason and with the same treatment: the page
 * resolves each contributing source's timestamp into epoch ms before it crosses, and a page that
 * emitted something else (a raw performance clock, a media clock) would store turn edges against a
 * clock the audio does not share — so an implausible skew is re-stamped Node-side and warned
 * LOUDLY rather than silently bound to nothing.
 *
 * Never throws into the capture path: this is a diagnostic, and a diagnostic that can break a
 * meeting is worse than no diagnostic at all.
 */
export function makeCsrcSink(
  telemetry?: CsrcCapableSink,
  warn: (m: string) => void = (m) => console.warn(m),
  /** The mixed lane's turn spine. Receives the RE-STAMPED record, so the pipeline and the stored
   *  sidecar can never disagree about when an edge happened — which is the whole basis on which a
   *  replay reproduces a live run. Optional: the gmeet lane has no server-side mix to consult. */
  consume?: (ev: { csrc: number; active: boolean; tMs: number; audioLevel?: number }) => void,
): {
  sink: (csrc: number, active: boolean, tMs?: number, audioLevel?: number, rtpTimestamp?: number) => void;
  crossed: () => number;
  stored: () => number;
} {
  let crossed = 0;
  let stored = 0;
  return {
    crossed: () => crossed,
    stored: () => stored,
    sink: (csrc: number, active: boolean, tMs?: number, audioLevel?: number, rtpTimestamp?: number): void => {
      crossed++;
      let t = tMs ?? Date.now();
      const skew = Math.abs(t - Date.now());
      if (skew > HINT_MAX_SKEW_MS) {
        warn(`[bot] csrc-clock-skew: transition tMs=${t} is ${Math.round(skew / 1000)}s off the epoch audio clock — page emitted a non-epoch timestamp; re-stamping (csrc=${csrc})`);
        t = Date.now();
      }
      const record: CsrcRecord = {
        type: 'csrc', t, csrc, active, lane: 'mixed',
        ...(typeof audioLevel === 'number' ? { audioLevel } : {}),
        ...(typeof rtpTimestamp === 'number' ? { rtpTimestamp } : {}),
      };
      if (typeof telemetry?.captureCsrc === 'function') {
        try { telemetry.captureCsrc(record); stored++; }
        catch { /* telemetry must not break capture */ }
      }
      // The lane consumes the edge AFTER it is stored: an edge the live run acted on but the
      // fixture does not contain is an edge no replay can ever reproduce.
      try { consume?.({ csrc, active, tMs: t, ...(typeof audioLevel === 'number' ? { audioLevel } : {}) }); }
      catch { /* the spine must not break capture either */ }
    },
  };
}

/** One Teams live-caption entry as it crosses the capture bridge — the caption lane's
 *  captured-signal record. Teams' OWN ASR attributes each caption to a named participant, so this
 *  is an ALTERNATIVE speaker-attribution SOURCE to the voice-level-outline hints; in THIS
 *  iteration it is observation data only. It is deliberately NOT a `HintEvent`: a hint feeds the
 *  name binder, and a caption that silently became one would change attribution behaviour under
 *  cover of a diagnostic. `t` shares the audio frames' epoch-ms clock. */
export interface TeamsCaptionRecord {
  type: 'caption';
  t: number;
  platform: 'teams';
  name: string;
  text: string;
  /** false ⇒ flushed mid-refinement at teardown (the meeting's tail), not settled. */
  stable: boolean;
  lane: 'mixed';
}

/** A telemetry sink that can ALSO store caption records. Structural and OPTIONAL: the recorder in
 *  telemetry.ts does not implement `captureCaption` yet (that file is another session's surface
 *  today), so captions currently land on the bot's log only, and the tape write lights up the
 *  moment the recorder grows the method — with no change here. Duck-typed through a named type
 *  rather than an inline cast, so the shape the recorder must implement is written down. */
export type CaptionCapableSink = TelemetrySink & { captureCaption?: (caption: TeamsCaptionRecord) => void };

/**
 * Build the Teams caption sink — the EXACT closure the bridge exposes as `__vexaTeamsCaption`,
 * factored out like makeSpeakerHintSink / makeTelemetryTap so it is offline-provable WITHOUT a
 * Playwright page.
 *
 * CLOCK CONTRACT is the hint sink's, for the same reason: the page stamps Date.now() (epoch), and
 * a page emitting a non-epoch time would store captions against a clock nothing else shares — so
 * an implausible skew is re-stamped Node-side and warned LOUDLY rather than silently kept.
 *
 * Never throws into the capture path: a caption is a diagnostic, and a diagnostic that can break a
 * meeting is worse than no diagnostic at all.
 */
export function makeTeamsCaptionSink(
  telemetry?: CaptionCapableSink,
  log: (m: string) => void = (m) => console.log(m),
  warn: (m: string) => void = (m) => console.warn(m),
  /** The caption AUTHOR, as name evidence for a transport track. Only the name and the time cross:
   *  the caption TEXT is Teams' ASR and never becomes our transcript. */
  consumeName?: (name: string, tMs: number) => void,
): { sink: (name: string, text: string, tMs?: number, stable?: boolean) => void; count: () => number; stored: () => number } {
  let count = 0;
  let stored = 0;
  return {
    count: () => count,
    stored: () => stored,
    sink: (name: string, text: string, tMs?: number, stable?: boolean): void => {
      count++;
      let t = tMs ?? Date.now();
      const skew = Math.abs(t - Date.now());
      if (skew > HINT_MAX_SKEW_MS) {
        warn(`[bot] caption-clock-skew: caption tMs=${t} is ${Math.round(skew / 1000)}s off the epoch audio clock — page emitted a non-epoch timestamp; re-stamping (name=${name})`);
        t = Date.now();
      }
      const record: TeamsCaptionRecord = { type: 'caption', t, platform: 'teams', name, text, stable: stable !== false, lane: 'mixed' };
      if (typeof telemetry?.captureCaption === 'function') {
        try { telemetry.captureCaption(record); stored++; }
        catch { /* telemetry must not break capture */ }
      }
      // Only a SETTLED caption is evidence: a mid-refinement entry can still change its author.
      if (record.stable && name) { try { consumeName?.(name, t); } catch { /* never break capture */ } }
      log(`[bot] teams-caption ${record.stable ? 'stable' : 'partial'} ${name}: ${text.slice(0, 80)}`);
    },
  };
}

/**
 * Captions are OFF by default (founder ruling, 2026-08-11), reversing the earlier default.
 *
 * Enabling them meant the bot clicking through the meeting's own UI — changing what the humans in
 * the room see — to obtain a name source. That trade is no longer worth making, because it was
 * measured and the source turned out to be redundant: replaying the m30, m34 and m36 tapes with
 * caption evidence WITHHELD produced byte-identical naming on all three (m34's per-track evidence
 * 7132/10372/82831 ms either way, m36's 46923 ms either way). Every track that captions helped name
 * was already named by the DOM and the roster. The lane paid a visible intrusion for nothing.
 *
 * The code path is kept and unchanged, behind this flag: set VEXA_TEAMS_ENABLE_CAPTIONS=1 to switch
 * it back on. It is a second, independent attribution source and is worth having available — for a
 * tenant whose outline never renders, or the next time a fixture argues for it. What it may no
 * longer do is switch itself on in somebody's meeting by default.
 *
 * A meeting where captions are already on is unaffected: this governs whether the BOT enables them,
 * not whether the reader consumes what it finds.
 */
const TEAMS_ENABLE_CAPTIONS = process.env.VEXA_TEAMS_ENABLE_CAPTIONS === '1';
/** How many times to try the menu path before giving up (each attempt is ~3 s of UI waits). */
const TEAMS_ENABLE_CAPTIONS_ATTEMPTS = Math.max(1, Number(process.env.VEXA_TEAMS_ENABLE_CAPTIONS_ATTEMPTS || 3));

/** Outcome of one enable attempt. `already-on` and `clicked` are successes; `failed` carries WHY,
 *  because "captions never appeared" has two very different causes — the menu path changed, or the
 *  tenant blocks captions — and only the reason distinguishes them on the first live run. */
export interface TeamsCaptionEnableResult {
  outcome: 'already-on' | 'clicked' | 'failed';
  reason?: 'more-button-unreachable' | 'menu-item-not-found' | 'error';
  /** Bounded, human-readable context (the visible menu items, or the error string). */
  detail?: string;
}

/** The typed observation the bridge logs when captions could not be switched on. It mirrors the
 * page-side caption observations' shape so both halves of the CC lane read as one stream in the
 * logs — it is declared HERE rather than imported because the bot may not import the page-side
 * brick (gate:isolation); page code crosses this boundary as plain values only. */
export interface TeamsCaptionsEnableFailedObservation {
  type: 'captions-enable-failed';
  platform: 'teams';
  signal: 'closed-caption';
  reason: 'more-button-unreachable' | 'menu-item-not-found' | 'error';
  attempts: number;
  detail?: string;
  tMs: number;
}

/**
 * Turn Teams live captions ON for the BOT's own session — ported from v0.10.6/v0.10.7
 * `services/vexa-bot/core/src/platforms/msteams/captions.ts` (byte-identical across those tags).
 *
 * Flow, both menu shapes the 0.10 bot met live:
 *   GUEST menu: More → "Captions" (a direct item)
 *   HOST menu:  More → "Language and speech" → "…live captions"
 * The renderer wrapper is checked afterwards, but its ABSENCE is not failure: Teams mounts it only
 * once somebody has spoken, so the authority on whether captions are live is the page-side
 * watcher's `captions-active`, not this function.
 *
 * NEVER throws to the caller and NEVER blocks the join: it is invoked fire-and-forget after the
 * capture path is already wired, an open menu is dismissed with Escape on any failure, and every
 * outcome is a log line.
 */
export async function enableTeamsLiveCaptions(page: Page, log: (m: string) => void = (m) => console.log(m)): Promise<TeamsCaptionEnableResult> {
  const WRAPPER = '[data-tid="closed-caption-renderer-wrapper"]';
  try {
    // The evaluate bodies below run IN THE BROWSER; this file type-checks against the Node lib, so
    // DOM globals are reached through globalThis exactly as the capture wiring does.
    if (await page.evaluate((sel) => !!(globalThis as any).document.querySelector(sel), WRAPPER)) {
      log('[bot] teams-captions: already enabled');
      return { outcome: 'already-on' };
    }
    // Step 1 — open the meeting toolbar's More menu (stable id first, aria-label fallbacks).
    try {
      await page.locator('#callingButtons-showMoreBtn, button[aria-label="More"], button[aria-label="More options"]')
        .first().click({ timeout: 8000 });
    } catch (e) {
      await page.keyboard.press('Escape').catch(() => { /* best-effort */ });
      return { outcome: 'failed', reason: 'more-button-unreachable', detail: String(e).slice(0, 200) };
    }
    await page.waitForTimeout(1000);

    // Step 2 — guest path (a direct "Captions" item) then host path (the submenu).
    const opened = await page.evaluate(() => {
      const doc = (globalThis as any).document;
      const items: any[] = Array.prototype.slice
        .call(doc.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'))
        .filter((el: any) => el.offsetParent !== null);
      for (const el of items) {
        const text = (el.textContent || '').trim().toLowerCase();
        if (text === 'captions' || text === 'show live captions' || text === 'turn on live captions') {
          el.click();
          return { clicked: (el.textContent || '').trim(), path: 'direct' as const, available: '' };
        }
      }
      for (const el of items) {
        const text = (el.textContent || '').toLowerCase();
        if (text.includes('language') && text.includes('speech')) {
          el.click();
          return { clicked: (el.textContent || '').trim(), path: 'submenu' as const, available: '' };
        }
      }
      return { clicked: null, path: 'none' as const, available: items.map((el: any) => (el.textContent || '').trim().slice(0, 40)).join(' | ') };
    });
    if (!opened.clicked) {
      // The visible menu is carried out verbatim (bounded): when the client renames the item, this
      // line is what tells us the new name instead of another blind live run.
      await page.keyboard.press('Escape').catch(() => { /* best-effort */ });
      return { outcome: 'failed', reason: 'menu-item-not-found', detail: opened.available.slice(0, 300) };
    }
    log(`[bot] teams-captions: clicked "${opened.clicked}" (${opened.path})`);
    await page.waitForTimeout(1000);

    if (opened.path === 'submenu') {
      const sub = await page.evaluate(() => {
        const doc = (globalThis as any).document;
        const items: any[] = Array.prototype.slice
          .call(doc.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'));
        for (const el of items) {
          if ((el.textContent || '').toLowerCase().includes('live captions') && el.offsetParent) {
            el.click();
            return (el.textContent || '').trim();
          }
        }
        return null;
      });
      log(sub ? `[bot] teams-captions: clicked submenu "${sub}"` : '[bot] teams-captions: live-captions item not in the submenu');
      await page.waitForTimeout(1500);
    }

    const on = await page.evaluate((sel) => !!(globalThis as any).document.querySelector(sel), WRAPPER);
    // The wrapper mounts only once somebody speaks, so "not yet" is not failure — the page-side
    // watcher reports captions-active the moment it appears, and IT is the authority.
    log(on
      ? '[bot] teams-captions: enabled (renderer wrapper present)'
      : '[bot] teams-captions: menu clicked, renderer wrapper not present yet — the watcher will report it');
    return { outcome: 'clicked' };
  } catch (e) {
    await page.keyboard.press('Escape').catch(() => { /* best-effort */ });
    return { outcome: 'failed', reason: 'error', detail: String(e).slice(0, 200) };
  }
}

/**
 * Run the enable flow at join with a short retry, then report. The retry exists because the Teams
 * toolbar is not present the instant the bot lands (the meeting UI settles over a few seconds) —
 * but it is BOUNDED and asynchronous, so a meeting whose tenant blocks captions costs us a few
 * seconds of background clicking and nothing else.
 *
 * A failure is a typed `captions-enable-failed` OBSERVATION on the same log stream as the
 * page-side caption observations — never an exception, never a lifecycle failure. The DOM
 * voice-level watcher is running in parallel throughout and is entirely unaffected by any of this.
 */
export async function runTeamsCaptionEnable(
  page: Page,
  log: (m: string) => void = (m) => console.log(m),
  attempts: number = TEAMS_ENABLE_CAPTIONS_ATTEMPTS,
  waitBetweenMs = 5000,
  /** Tee the typed failure into the fixture as well as the log. Optional: the enable flow must
   *  work identically for a bot that is not taping. */
  onObservation?: (source: string, obs: unknown, tMs?: number) => void,
): Promise<TeamsCaptionEnableResult> {
  let last: TeamsCaptionEnableResult = { outcome: 'failed', reason: 'error', detail: 'not attempted' };
  for (let i = 1; i <= attempts; i++) {
    last = await enableTeamsLiveCaptions(page, log).catch((e): TeamsCaptionEnableResult =>
      ({ outcome: 'failed', reason: 'error', detail: String(e).slice(0, 200) }));
    if (last.outcome !== 'failed') {
      log(`[bot] teams-captions: enable ${last.outcome} on attempt ${i}/${attempts}`);
      return last;
    }
    log(`[bot] teams-captions: enable attempt ${i}/${attempts} failed (${last.reason}) — ${last.detail ?? ''}`);
    if (i < attempts) await page.waitForTimeout(waitBetweenMs).catch(() => { /* page may be closing */ });
  }
  const observation: TeamsCaptionsEnableFailedObservation = {
    type: 'captions-enable-failed',
    platform: 'teams',
    signal: 'closed-caption',
    reason: last.reason ?? 'error',
    attempts,
    detail: last.detail,
    tMs: Date.now(),
  };
  // Same line shape as the page-side caption observations, so the CC lane reads as one stream —
  // and into the fixture beside them, so a tape says whether captions were off because the tenant
  // blocks them or because our menu path rotted.
  log(`[TeamsCaptions] observation ${JSON.stringify(observation)}`);
  try { onObservation?.('teams-captions', observation, observation.tMs); } catch { /* never breaks the join */ }
  log('[bot] teams-captions: could not switch captions on — continuing on the voice-level-outline '
    + 'watcher alone (the join and the transcript are unaffected)');
  return last;
}

/** Path (in the bot container image) to the prebuilt page-side capture bundle that defines
 *  window.VexaBrowserUtils (createGmeetCapture / createGmeetSpeakers / mixed taps). Mirrors
 *  production's browser-utils.global.js; injected via addInitScript so it is present on every
 *  navigation. Overridable by env for the VM harness. */
const BROWSER_UTILS_PATH = process.env.VEXA_BROWSER_UTILS_PATH ?? '/app/browser-utils.global.js';

/** A handle to the live browser the bot drives. The composition root closes it on teardown. */
export interface BrowserSession {
  context: BrowserContext;
  page: Page;
  close(): Promise<void>;
}

/**
 * Launch the browser the bot joins through. Authenticated bots restore the persistent profile
 * from S3 first (so they join as a signed-in user); guest bots launch a fresh persistent context.
 * Always uses getJoinBrowserArgs() (the join lane's canonical flag set) merged with the
 * remote-browser auth args, so the page the JoinDriver receives is configured identically to
 * what @vexa/join expects.  // L4 (O6/VM): live-validated against a real meeting.
 */
export async function launchBrowser(inv: Invocation): Promise<BrowserSession> {
  // Every bot gets its OWN profile dir — concurrent bots sharing one dir die on Chromium's
  // SingletonLock (#478: joining → failed <1s, "Opening in existing browser session").
  // Authenticated: restore the S3 userdata into this bot's dir before launch (index.ts:2313–2347).
  const dataDir = makeEphemeralProfileDir();
  const s3Config = {
    userdataS3Path: inv.userdataS3Path,
    s3Endpoint: inv.s3Endpoint,
    s3Bucket: inv.s3Bucket,
    s3AccessKey: inv.s3AccessKey,
    s3SecretKey: inv.s3SecretKey,
  };
  if (inv.authenticated && inv.userdataS3Path) {
    // Fail-loud restore: an unreachable/misconfigured store surfaces as a typed SessionSyncError
    // naming the session-restore step (the composition root drives it to a clean terminal failed)
    // — an authenticated bot never silently proceeds to join signed-out on a failed restore.
    syncBrowserDataFromS3(s3Config, dataDir);
    cleanStaleLocks(dataDir);
  }

  // getAuthenticatedBrowserArgs() is the minimal clean set remote-browser uses for signed-in
  // joins; getJoinBrowserArgs() adds the fake-device / autoplay flags the join lane needs. The
  // join args win on conflict (later wins in Chromium arg parsing).
  const args = [...getAuthenticatedBrowserArgs(), ...getJoinBrowserArgs()];
  const { context, page } = await launchPersistentBrowser({ dataDir, args });

  // Voice-agent gate the page reads to decide whether to keep the mic hot (production parity).
  await context.addInitScript(`window.__vexa_voice_agent_enabled = ${!!inv.voiceAgentEnabled};`);
  // Inject the page-side capture bundle on every navigation (defines window.VexaBrowserUtils).
  await context.addInitScript({ path: BROWSER_UTILS_PATH }).catch(() => {
    // The bundle may be loaded by other means in some images; capture wiring degrades to the
    // inline fallback below. Never fatal at launch.
  });

  // #593 A1: a page-context global fault logger, installed at document-start on EVERY frame/nav so
  // gmeet + teams + zoom all inherit it. Before this, the only error-shaped line on the bot's stdout
  // for a Teams join was the platform's OWN `Unhandled rejection {isTrusted:true}` — a bare DOM Event
  // that misdirected #593 (it's Teams' VQE worklet, unrelated to our Node throw). This handler names
  // the actual reason (message + stack) AND, for a bare Event, its type/target — so the {isTrusted}
  // line is finally identified rather than mistaken for the cause. Non-fatal at launch (like neighbors).
  await context.addInitScript(`(() => {
    var report = function (m) { try { (window.logBot || console.error)('[page-fault] ' + m); } catch (e) {} };
    window.addEventListener('unhandledrejection', function (ev) {
      var r = ev && ev.reason;
      var msg = (r && (r.message || r.name)) ? ((r.name || 'Error') + ': ' + (r.message || '')) : String(r);
      var stack = (r && r.stack) ? r.stack : '(no stack)';
      report('unhandledrejection: ' + msg + ' :: ' + stack);
    });
    window.addEventListener('error', function (ev) {
      var msg;
      if (ev && ev.error && (ev.error.message || ev.error.stack)) {
        msg = (ev.error.name || 'Error') + ': ' + (ev.error.message || '') + ' :: ' + (ev.error.stack || '(no stack)');
      } else {
        var t = ev && ev.target;
        var tag = t && (t.tagName || t.nodeName);
        var src = t && (t.src || t.href || t.currentSrc);
        msg = 'event type=' + (ev && ev.type) + (tag ? ' target=' + tag : '') + (src ? ' src=' + src : '') + ' isTrusted=' + (ev && ev.isTrusted);
      }
      report('error: ' + msg);
    });
  })();`).catch(() => { /* never fatal at launch */ });

  // Zoom/Teams expose NO per-participant <audio> in the DOM — install the WebRTC hook so each
  // remote audio track is mirrored into a hidden <audio> element (→ __vexaCapturedRemoteAudioStreams)
  // the mixed lane combines. Jitsi rides the same hook: its remote audio also arrives as WebRTC
  // tracks, and hooking RTCPeerConnection is version-proof where its DOM <audio> ids are not.
  // MUST run before the page builds its RTCPeerConnections; addInitScript
  // runs at document-start, after the bundle above has defined window.VexaBrowserUtils. (L4 — Zoom/Teams.)
  if (isMixedLanePlatform(inv.platform)) {
    await context.addInitScript(
      `try { window.VexaBrowserUtils && window.VexaBrowserUtils.installRemoteAudioHook && window.VexaBrowserUtils.installRemoteAudioHook({}); } catch (e) {}`,
    ).catch(() => { /* non-fatal */ });
  }

  // Observability (L4): route the page-side capture's log(m) → container stdout. gmeet-capture
  // calls window.logBot?.(...) ("stream N connected", "capture started with N stream(s)", …); without
  // exposing it those vanish and the capture is invisible. context.exposeFunction persists across the
  // navigation to the meeting URL. Also forward page console errors/capture markers so faults surface.
  await context.exposeFunction('logBot', (m: string) => console.log(`[page] ${m}`)).catch(() => { /* already registered */ });
  page.on('console', (msg) => {
    const t = msg.text();
    if (/perspeaker|capture|stream|vexabrowser|audiocontext|error|fail/i.test(t)) console.log(`[page-console:${msg.type()}] ${t}`);
  });

  return {
    context,
    page,
    async close() {
      await context.close().catch(() => { /* best-effort */ });
      // Write-back on clean teardown (#725): Google rotates session cookies during use, so the
      // durable copy is refreshed from the LIVE profile dir after the context flushes — the next
      // spawn restores the freshest state instead of a decaying snapshot. Clean teardown only:
      // a SIGKILL never reaches close(), so a hard-killed meeting keeps the last durable copy.
      // Failures are attributed warnings, bounded per upload — teardown never hangs on S3.
      if (inv.authenticated && inv.userdataS3Path) {
        try {
          syncBrowserDataToS3(s3Config, dataDir);
        } catch (e) {
          console.error(`[bot] session write-back failed (durable copy stays at last restore): ${String(e)}`);
        }
      }
      removeProfileDir(dataDir);   // per-bot dir — leaking one per bot fills the disk in vexa-lite
    },
  };
}

/**
 * Wire the page-side capture to pipeline.feedAudio. Exposes the Node bridge binding
 * `__vexaPerSpeakerAudioData(speakerIndex, samples[], tsMs?)` and starts the in-page capture
 * (preferring the shared VexaBrowserUtils module, with production's inline fallback). For the
 * mixed lane (Zoom/Teams) it instead pumps the single mixed stream + active-speaker hints.
 * Returns a stop fn that tears the page-side capture down.
 *   // L4 (O6/VM): live-validated against a real meeting.
 *   Ported from services/vexa-bot/core/src/index.ts:1930, 1947–1957, 1598–1605.
 */
export async function startCaptureBridge(
  page: Page,
  inv: Invocation,
  pipeline: BotPipeline,
  telemetry?: TelemetrySink,
  /** In-meeting chat sink (jitsi lane) — each captured chat message crosses here;
   *  the composition root publishes it as a transcript.v1 `source:'chat'` segment. */
  onChat?: (sender: string, text: string) => void,
  /** Active-phase silence signal. It remains unavailable until page capture reports ready. */
  activity?: RemoteAudioActivityTap,
): Promise<() => Promise<void>> {
  const mixed = isMixedLanePlatform(inv.platform);
  const jitsi = inv.platform === 'jitsi';
  const lane: 'gmeet' | 'mixed' = mixed ? 'mixed' : 'gmeet';

  // JaaS/8x8.vc-backed Jitsi deployments (Brave Talk and other white-labeled embeds) render the
  // whole conference — media elements included — inside a cross-origin <iframe>; page.evaluate()
  // only ever reaches the top frame, so capture setup/control must target the resolved frame
  // instead. Every other platform (and stock/self-hosted Jitsi, which renders at the top level)
  // keeps targeting `page` exactly as before — resolveConferenceFrame only runs for jitsi.
  const captureTarget: any = jitsi ? await resolveConferenceFrame(page) : page;

  // ── O-TEL-1 raw-signal tap (a DUAL-sink) ──────────────────────────────────────────────────
  // When a TelemetrySink is wired, tee each raw frame to it BEFORE the pipeline consumes it, so a
  // live bug's exact signal is stored as captured-signal.v1 and replays offline (O-TEL-2). The tap
  // is OPTIONAL + zero-overhead when unset (makeTelemetryTap short-circuits to a single truthiness
  // check), so the proven O6 capture path is byte-for-byte unchanged. captureFrame is fire-and-forget.
  const tee = makeTelemetryTap(lane, telemetry);
  const observeRemoteAudio = makeRemoteAudioEnergyTap(activity);

  // ── Node-side frame sink: one capture.v1 frame crossing the Playwright boundary. ──
  // The page serializes PCM as a plain number[] (Array.from(Float32Array)); we restore the
  // Float32Array and stamp the capture time if the page didn't supply one (production stamps
  // Date.now() on the Node side — index.ts:1598–1605).
  const onPerSpeakerAudio = (speakerIndex: number, samples: number[], tsMs?: number): void => {
    const pcm = new Float32Array(samples);
    const ts = tsMs ?? Date.now();
    observeRemoteAudio(pcm);
    tee(speakerIndex, pcm, ts);                                 // O-TEL-1: tap BEFORE the pipeline
    if (mixed) pipeline.feedMixedAudio(pcm, ts);
    else pipeline.feedAudio(speakerIndex, undefined, pcm, ts); // glow name is bound page-side in the v1 producer; channel index here
  };
  // gmeet: the v1 producer stamps the glow name page-side; this named variant carries it through.
  const onNamedAudio = (channel: number, glowName: string | undefined, samples: number[], tsMs?: number): void => {
    const pcm = new Float32Array(samples);
    const ts = tsMs ?? Date.now();
    observeRemoteAudio(pcm);
    tee(channel, pcm, ts, glowName);                            // O-TEL-1: tap BEFORE the pipeline
    pipeline.feedAudio(channel, glowName, pcm, ts);
  };
  // mixed lane "who is lit" hint (Zoom/Teams active-speaker → the namer's time window).
  // Epoch-clock-guarded + counted; see makeSpeakerHintSink for the clock contract.
  const { sink: onSpeakerHint, crossed: hintsBridgeCrossed } = makeSpeakerHintSink(pipeline, undefined, telemetry);
  // Teams live captions: Teams' own ASR names the speaker, which is a second, independent naming
  // source beside the voice-level outline. The AUTHOR (never the text) is now offered to the lane
  // as evidence for a transport TRACK — still not to pipeline.recordHint, because a caption is not
  // a turn and the binder's per-turn window is exactly the machinery the track spine avoids.
  const { sink: onTeamsCaption, count: captionsBridgeCrossed } = makeTeamsCaptionSink(
    telemetry, undefined, undefined,
    mixed ? (name, tMs) => pipeline.recordCaptionName?.(name, tMs) : undefined,
  );
  // The transport sensor's edges (mixed lane, every platform): RTP contributing-source
  // activations/deactivations. They are stored AND fed to the lane, where they are the turn SPINE —
  // never a name. A1 deliberately stopped short of this hop; A2 is the hop.
  const { sink: onCsrc, crossed: csrcBridgeCrossed } = makeCsrcSink(
    telemetry, undefined,
    mixed ? (ev) => pipeline.recordTransportEvent?.(ev) : undefined,
  );
  // Every typed observation the capture path produces, teed to the fixture instead of dying with
  // the pod. Page-side producers call __vexaObservation alongside their existing log line; the
  // Node-side ones (the caption-enable outcome) call this sink directly.
  const { sink: onObservation, crossed: obsBridgeCrossed } = makeObservationSink(
    lane, telemetry, undefined,
    mixed ? (name, tMs) => pipeline.recordRosterName?.(name, tMs) : undefined,
    mixed ? (named, participants, tMs) => pipeline.recordRosterCoverage?.(named, participants, tMs) : undefined,
  );
  // C1: the four hint hops on one periodic, cumulative counter line —
  // page-emitted lives in the page console ([TeamsSpeakers]/[JitsiSpeakers] logs);
  // bridge-crossed / pipeline-received / binder matched|missed are Node-side.
  const countersTimer = mixed ? setInterval(() => {
    const c = pipeline.hintCounters;
    console.log(`[bot] hint-counters bridge-crossed=${hintsBridgeCrossed()} pipeline-received=${c?.received ?? 0} binder-matched=${c?.matched ?? 0} binder-missed=${c?.missed ?? 0} teams-captions=${captionsBridgeCrossed()} csrc-crossed=${csrcBridgeCrossed()} observations=${obsBridgeCrossed()}`);
  }, 30_000) : null;
  countersTimer?.unref?.();   // observability only — never holds the process open

  await page.exposeFunction('__vexaPerSpeakerAudioData', onPerSpeakerAudio).catch((e: Error) => {
    if (!String(e.message).includes('already registered')) throw e;
  });
  await page.exposeFunction('__vexaNamedAudioData', onNamedAudio).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaSpeakerHint', onSpeakerHint).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaTeamsCaption', onTeamsCaption).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaCsrc', onCsrc).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaObservation', onObservation).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaRemoteAudioReady', (): void => activity?.ready()).catch((e: Error) => {
    if (!String(e.message).includes('already registered')) throw e;
  });
  // #1192: how many remote streams the mix is CURRENTLY connected to and receiving from. Frame
  // arrival alone cannot separate an emptied room and a dead capture chain; this is the bit that
  // separates them, and it exists only on the page. Optional by design — the gmeet lane never
  // calls it, so its aloneness behaviour is unchanged.
  await page.exposeFunction('__vexaStreamPresence', (count: number): void => activity?.observeStreamPresence?.(count)).catch((e: Error) => {
    if (!String(e.message).includes('already registered')) throw e;
  });
  // jitsi chat → the embedder's sink (a transcript.v1 `chat` segment at the composition root).
  await page.exposeFunction('__vexaChatMessage', (sender: string, text: string): void => {
    try { onChat?.(sender, text); } catch (e) { console.error(`[bot] chat sink rejected: ${String(e)}`); }
  }).catch(() => { /* optional */ });

  // ── Start the page-side capture (VexaBrowserUtils preferred; production inline fallback). ──
  // The body of this callback runs IN THE BROWSER (Playwright serializes it); DOM globals are
  // reached via globalThis (this file type-checks against the Node lib — no DOM types here).
  await captureTarget.evaluate(async ({ isMixed, isJitsi, isTeams, isZoom, botName, mainAudioGraceMs, mainAudioSilenceMs, mainAudioEnergyRms }: any) => {
    const w = (globalThis as any) as Record<string, any>;
    if (isMixed) {
      // Zoom/Teams: installRemoteAudioHook (installed pre-nav) mirrors each remote WebRTC audio
      // track into w.__vexaCapturedRemoteAudioStreams. Combine them into ONE live stream (an
      // AudioContext destination), keep connecting late-arriving tracks via a rescan (a participant
      // who speaks later), and feed that single mix to the mixed lane (pyannote re-separates speakers).
      // Teams delivers the COMPLETE meeting audio as a single server-side mix whose track id is prefixed
      // "mainAudio" — witnessed live: the standard web client receives exactly ONE audio receiver. The
      // bot is ALSO handed a redundant track (e.g. a dominant-speaker copy) whose audio is already inside
      // that mix; combining both double-feeds every word to the transcriber → repeated words. So on Teams
      // mix ONLY the mainAudio track. Jitsi keeps combining all tracks (its topology isn't witnessed).
      // #1192 — report how many connected remote streams are LIVE and carrying data, every rescan.
      // `__vexaMixSeen` cannot answer this: it is a monotonic id ledger (dedupe for the connect
      // loop), so it counts streams EVER connected — crossing that number would leave a bot that
      // once saw a participant unable to resolve left_alone for the rest of the meeting. This walks
      // the connected stream objects instead and counts the ones whose audio track is `live` and
      // not `muted` (a remote track mutes when packets stop arriving). Node-side the count is
      // sticky over a staleness window, so the DTX gap between talk spurts cannot read as an
      // empty room.
      const reportStreamPresence = (): void => {
        let live = 0;
        for (const s of (w.__vexaMixStreamRefs || []) as Array<any>) {
          try {
            const tracks = s?.getAudioTracks ? s.getAudioTracks() : [];
            for (const t of tracks) {
              if (t && t.readyState === 'live' && t.muted !== true) { live++; break; }
            }
          } catch { /* a stream may have been torn down mid-walk */ }
        }
        try { w.__vexaStreamPresence?.(live); } catch { /* presence must never break capture */ }
      };
      const setupMix = (): void => {
        reportStreamPresence();
        let streams = (w.__vexaCapturedRemoteAudioStreams || []) as Array<any>;
        if (isTeams && streams.length) {
          // selectTeamsMixStreams (bundled via VexaBrowserUtils) prefers the server mix and FAILS OPEN
          // if it never appears — see its doc for why an unconditional preference is the more
          // dangerous bug. Kept out of this callback so the page and its unit test run one
          // implementation; a hand-copied twin here would drift from the test on the first edit.
          const select = w.VexaBrowserUtils?.selectTeamsMixStreams;
          if (select) {
            // PRESENCE IS NOT LIVENESS. A mainAudio track that appears and then carries pure silence
            // passes the absence check forever, so the bot captures nothing while the real tracks sit
            // mirrored and unused — and the lane's own csrc→pyannote watchdog cannot rescue it,
            // because that demotion needs ENERGETIC audio to fire. Latch the verdict once (so the
            // pick cannot oscillate as the fallback's own audio arrives) and keep reporting it.
            const provedSilent = w.VexaBrowserUtils?.mainAudioProvedSilent;
            if (!w.__vexaTeamsMainAudioDead && provedSilent?.({
              captureStartedMs: w.__vexaMixCaptureStartedMs ?? null,
              energeticMs: w.__vexaMixEnergeticMs || 0,
              nowMs: Date.now(),
              silenceMs: mainAudioSilenceMs,
            })) {
              w.__vexaTeamsMainAudioDead = true;
              // SAY IT. The counters showed a healthy-looking bot for the whole of 26040 while it
              // captured nothing; the only trace was a number nobody was reading. A capture that
              // has proved dead is a typed observation, so it lands in the tape beside the silence
              // it is describing and a replay can find it without a human noticing first.
              w.logBot?.('[mixed] MAIN-AUDIO ABSENT — the picked mix produced no sound; falling back to every track');
              w.__vexaObservation?.('mixed', {
                type: 'main-audio-absent',
                platform: isTeams ? 'teams' : 'mixed',
                capturedMs: w.__vexaMixCaptureStartedMs ? Date.now() - w.__vexaMixCaptureStartedMs : 0,
                energeticMs: w.__vexaMixEnergeticMs || 0,
                framesSeen: w.__vexaMixFrames || 0,
                silenceMs: mainAudioSilenceMs,
                tMs: Date.now(),
              }, Date.now());
            }
            const sel = select(streams, {
              firstMissMs: w.__vexaTeamsNoMainSince ?? null,
              nowMs: Date.now(),
              graceMs: mainAudioGraceMs,
              mainAudioSilent: !!w.__vexaTeamsMainAudioDead,
              mainAudioCapturedMs: w.__vexaMixCaptureStartedMs ? Date.now() - w.__vexaMixCaptureStartedMs : 0,
            });
            if (sel.outcome === 'main-audio') { w.__vexaTeamsNoMainSince = null; }
            else { w.__vexaTeamsNoMainSince = w.__vexaTeamsNoMainSince || Date.now(); }
            // Re-emitted on EVERY falling-back rescan, deliberately: the latched one-shot warning it
            // replaces meant a bot capturing the wrong thing said so once and looked healthy after.
            if (sel.observation) {
              w.logBot?.('[mixed] observation ' + JSON.stringify(sel.observation));
              w.__vexaObservation?.('mixed', sel.observation, Date.now());
            }
            streams = sel.streams;
          }
        }
        if (!streams.length) return;
        // How many streams the mix is built from is a FACT about the meeting's topology, not a
        // log line: one server-side mix behaves nothing like N per-participant tracks, and the
        // difference decides how a tape may be read. Emitted on the first mix and again on every
        // CHANGE (a late track joining), so the tape carries the shape over time rather than one
        // snapshot taken before everyone arrived.
        const reportTopology = (): void => {
          const n = w.__vexaMixSeen ? w.__vexaMixSeen.size : 0;
          if (w.__vexaMixTopology === n) return;
          w.__vexaMixTopology = n;
          w.__vexaObservation?.('mixed', { type: 'mix-topology', streams: n, tMs: Date.now() }, Date.now());
        };
        if (!w.__vexaMixCtx) {
          w.__vexaMixCtx = new (globalThis as any).AudioContext({ sampleRate: 16000 });
          w.__vexaMixCtx.resume?.();
          w.__vexaMixDest = w.__vexaMixCtx.createMediaStreamDestination();
          w.__vexaMixSeen = new Set();
        }
        for (const s of streams) {
          if (!s || w.__vexaMixSeen.has(s.id)) continue;
          try {
            w.__vexaMixCtx.createMediaStreamSource(s).connect(w.__vexaMixDest);
            w.__vexaMixSeen.add(s.id);
            (w.__vexaMixStreamRefs = w.__vexaMixStreamRefs || []).push(s);
            w.logBot?.('[mixed] connected remote stream ' + w.__vexaMixSeen.size);
            reportTopology();
          } catch { /* a stream may not be connectable yet */ }
        }
        if (!w.__vexaMixedCapture && w.__vexaMixSeen.size && w.VexaBrowserUtils?.createMixedAudioCapture) {
          w.__vexaMixedCapture = true; // guard re-entry while the async create resolves
          // Meter the mix as it is captured: the silence verdict above needs to know whether the
          // stream we PICKED ever carried sound, and this callback is the only place that sees it.
          const meterAndForward = (pcm: Float32Array): void => {
            // Frames seen at all — the difference between a mix that is QUIET and one that is not
            // there. Both end in the same fallback, but a fixture should not have to infer which.
            w.__vexaMixFrames = (w.__vexaMixFrames || 0) + 1;
            let sum = 0;
            for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
            if (pcm.length && Math.sqrt(sum / pcm.length) >= mainAudioEnergyRms) {
              w.__vexaMixEnergeticMs = (w.__vexaMixEnergeticMs || 0) + (pcm.length / 16000) * 1000;
            }
            w.__vexaPerSpeakerAudioData(0, Array.from(pcm));
          };
          Promise.resolve(w.VexaBrowserUtils.createMixedAudioCapture(w.__vexaMixDest.stream, meterAndForward))
            .then((cap: any) => {
              w.__vexaMixedCapture = cap;
              // THE CLOCK STARTS WHEN CAPTURE STARTS, NOT WHEN AUDIO ARRIVES.
              //
              // The silence guard must not depend on the very thing it exists to detect the absence
              // of. Started here, elapsed time is measured against capture being ALIVE, so a picked
              // track that delivers NOTHING still arms the capture-all fallback: a mix that carries
              // no frames at all and a mix that carries silent frames reach the same verdict.
              //
              // Anchoring the clock in the PCM callback instead would make absence-of-frames
              // invisible — no callback, no start time, and every rescan reading "nothing captured
              // yet, so not evidence of silence" while the meeting produces no transcript at all.
              // Silence-with-frames is unaffected either way: it sets energeticMs > 0 and exits early.
              if (w.__vexaMixCaptureStartedMs === undefined || w.__vexaMixCaptureStartedMs === null) {
                w.__vexaMixCaptureStartedMs = Date.now();
              }
              return cap?.start?.();
            })
            .then(async () => {
              await w.__vexaRemoteAudioReady?.();
              w.logBot?.('[mixed] capture started over ' + w.__vexaMixSeen.size + ' stream(s)');
            })
            .catch((e: any) => { w.__vexaMixedCapture = null; w.logBot?.('[mixed] capture start failed: ' + String(e)); });
        }
      };
      setupMix();
      w.__vexaMixRescan = (globalThis as any).setInterval(setupMix, 2000); // pick up late-arriving tracks

      // ── the transport sensor (mixed lane, EVERY platform) ────────────────────────────────────
      // RTP labels the server-side mix with the sources it mixed; the sensor turns that into turn
      // EDGES instead of the two inferences the lane makes today. It lives here rather than in a
      // platform branch because it has no platform vocabulary in it — a client that mixes
      // server-side reports contributing sources whatever its DOM looks like — and it reuses the
      // remote-audio hook's peer-connection registry, so it patches nothing and races nothing.
      // A page with no peer connections (the negative path, and the common one) yields zero
      // transitions and zero errors. The gmeet lane never reaches this branch: it captures each
      // participant separately and has no mix to disambiguate.
      if (w.VexaBrowserUtils?.createCsrcPoll && !w.__vexaCsrcPoll) {
        try {
          w.__vexaCsrcPoll = w.VexaBrowserUtils.createCsrcPoll({
            // DIAGNOSTIC ONLY: this crosses to the captured-signal tape + the counters. It is NOT
            // wired to __vexaSpeakerHint — an observation that quietly became a hint would change
            // speaker attribution while claiming to merely watch it.
            onTransition: (t: { csrc: number; active: boolean; tMs: number; audioLevel?: number; rtpTimestamp?: number }) =>
              w.__vexaCsrc?.(t.csrc, t.active, t.tMs, t.audioLevel, t.rtpTimestamp),
            onObservation: (o: Record<string, unknown>) => {
              w.logBot?.('[Csrc] observation ' + JSON.stringify(o));
              w.__vexaObservation?.('csrc', o, Date.now());
            },
            log: (m: string) => w.logBot?.('[Csrc] ' + m),
          });
        } catch (e: any) {
          w.__vexaCsrcPoll = null;
          w.logBot?.('[Csrc] init failed — continuing without transport observation: ' + String(e));
        }
      } else if (!w.VexaBrowserUtils?.createCsrcPoll) {
        w.logBot?.('[Csrc] not in the browser bundle — continuing without transport observation');
      }

      if (isTeams) {
        // Teams contributes the WHO signal the mixed audio can't carry: the voice-level
        // "blue-square" outline watcher (@vexa/teams-capture — the SAME module the desktop
        // extension runs) emits debounced speaking start/stop per participant; each crosses
        // to the Node side as a speaker hint (epoch tMs) and the pipeline stamps the
        // platform's 'dom-outline' kind at its wiring seam.
        if (w.VexaBrowserUtils?.createTeamsSpeakers && !w.__vexaTeamsSpeakers) {
          w.__vexaTeamsSpeakers = w.VexaBrowserUtils.createTeamsSpeakers({
            selfName: botName,
            log: (m: string) => w.logBot?.('[TeamsSpeakers] ' + m),
            onSpeaking: (name: string, _id: string, isEnd: boolean, tMs: number) =>
              w.__vexaSpeakerHint?.(name, tMs, isEnd),
            // Typed producer DIAGNOSTICS — signal-absent / indicator-fired /
            // indicator-silent / name-unresolved. They are logged, never turned
            // into a hint: a diagnostic that becomes a name is a fabricated name.
            onObservation: (o: Record<string, unknown>) => {
              w.logBot?.('[TeamsSpeakers] observation ' + JSON.stringify(o));
              w.__vexaObservation?.('teams-speakers', o, Date.now());
            },
          });
          // Coverage + liveness of the WHO signal, alongside the hint counters.
          // The failure this exists for was silent: 3 of 4 tiles unobservable and
          // zero speaking transitions, with nothing in the logs saying so.
          w.__vexaTeamsHealthTimer = (globalThis as any).setInterval(() => {
            try {
              const h = w.__vexaTeamsSpeakers?.health?.();
              if (h) {
                w.logBot?.(
                  `[TeamsSpeakers] health found=${h.found} observable=${h.observable} `
                  + `named=${h.named} name-unresolved=${h.nameUnresolved} transitions=${h.transitions}`,
                );
              }
            } catch { /* observability must never break capture */ }
            try {
              const c = w.__vexaTeamsCaptions?.health?.();
              if (c) {
                w.logBot?.(
                  `[TeamsCaptions] health present=${c.present} wrapper=${c.wrapperSelector ?? 'none'} `
                  + `authors=${c.authors} texts=${c.texts} emitted=${c.emitted} unresolved=${c.unresolved} self=${c.self}`,
                );
              }
            } catch { /* observability must never break capture */ }
          }, 30_000);
        }
        // Teams live captions — a SECOND, independent attribution source, read only when the
        // meeting already has captions on. We never switch them on: clicking meeting UI mid-join
        // is how joins break, and this iteration buys observation, not behaviour. Everything about
        // it is best-effort — an init throw is caught, logged ONCE, and the bot carries on exactly
        // as it does today. The one thing the first live meeting must answer is whether the 0.10
        // selectors still match, which is why captions-found / captions-absent are logged.
        if (w.VexaBrowserUtils?.createTeamsCaptions && !w.__vexaTeamsCaptions) {
          try {
            w.__vexaTeamsCaptions = w.VexaBrowserUtils.createTeamsCaptions({
              selfName: botName,
              log: (m: string) => w.logBot?.('[TeamsCaptions] ' + m),
              // DIAGNOSTIC ONLY: this crosses to the captured-signal tape + the log. It is NOT
              // wired to __vexaSpeakerHint — a caption that quietly became a hint would change
              // speaker attribution while claiming to observe it.
              onCaption: (c: { speaker: string; text: string; tMs: number; stable: boolean }) =>
                w.__vexaTeamsCaption?.(c.speaker, c.text, c.tMs, c.stable),
              onObservation: (o: Record<string, unknown>) => {
                w.logBot?.('[TeamsCaptions] observation ' + JSON.stringify(o));
                w.__vexaObservation?.('teams-captions', o, Date.now());
              },
            });
          } catch (e: any) {
            w.__vexaTeamsCaptions = null;
            w.logBot?.('[TeamsCaptions] init failed — continuing without captions: ' + String(e));
          }
        } else if (!w.VexaBrowserUtils?.createTeamsCaptions) {
          w.logBot?.('[TeamsCaptions] not in the browser bundle — continuing without captions');
        }
      }
      if (isJitsi) {
        // Jitsi contributes the WHO + chat signals the mixed audio can't carry:
        // dominant-speaker changes name the pyannote clusters ('dom-active' hints),
        // and chat messages cross to the Node side as transcript `chat` segments.
        if (w.VexaBrowserUtils?.createJitsiSpeakers && !w.__vexaJitsiSpeakers) {
          w.__vexaJitsiSpeakers = w.VexaBrowserUtils.createJitsiSpeakers({
            selfName: botName,
            log: (m: string) => w.logBot?.('[JitsiSpeakers] ' + m),
            onSpeaking: (name: string, _id: string, isEnd: boolean, tMs: number) =>
              w.__vexaSpeakerHint?.(name, tMs, isEnd),
          });
        }
        if (w.VexaBrowserUtils?.createJitsiChat && !w.__vexaJitsiChat) {
          w.__vexaJitsiChat = w.VexaBrowserUtils.createJitsiChat({
            log: (m: string) => w.logBot?.('[JitsiChat] ' + m),
            onMessage: (m: { sender: string; text: string }) => w.__vexaChatMessage?.(m.sender, m.text),
          });
        }
      }
      if (isZoom) {
        // Zoom contributes the WHO signal the mixed audio can't carry: the active-speaker
        // DOM watcher (poll + flicker debounce lives in @vexa/zoom-capture) emits name
        // transitions → __vexaSpeakerHint → pipeline.recordHint, which labels them
        // 'dom-active' (Zoom's true kind — the mixed lane's DOM-active lag model).
        // Timestamps are page Date.now() = epoch ms, the same clock Node stamps with.
        if (w.VexaBrowserUtils?.createZoomSpeakers && !w.__vexaZoomSpeakers) {
          let lastActive: string | null = null;
          w.__vexaZoomSpeakers = w.VexaBrowserUtils.createZoomSpeakers({
            selfName: botName,
            log: (m: string) => w.logBot?.('[ZoomSpeakers] ' + m),
            onSpeakerChange: (name: string | null) => {
              const tMs = Date.now();
              if (name) w.__vexaSpeakerHint?.(name, tMs, false);           // start / heartbeat re-assert
              else if (lastActive) w.__vexaSpeakerHint?.(lastActive, tMs, true); // nobody lit → close the turn
              lastActive = name;
            },
          });
        }
      }
      return;
    }
    // gmeet lane: per-channel capture + glow attribution (the SAME module the extension runs).
    if (w.VexaBrowserUtils?.createGmeetCapture && !w.__vexaGmeetCapture) {
      w.__vexaGmeetSpeakers = w.__vexaGmeetSpeakers
        ?? w.VexaBrowserUtils.createGmeetSpeakers?.({ log: (m: string) => w.logBot?.('[PerSpeaker] ' + m) });
      w.__vexaGmeetCapture = w.VexaBrowserUtils.createGmeetCapture({
        log: (m: string) => w.logBot?.('[PerSpeaker] ' + m),
        onAudio: (index: number, pcm: Float32Array) => {
          w.__vexaGmeetSpeakers?.reportTrackAudio?.(index);
          // Bind the glow name at capture time (the v1 producer's inversion): exactly-one-lit ⇒ name.
          const lit: string[] = w.__vexaGmeetSpeakers?.litNames?.() ?? [];
          const glow = lit.length === 1 ? lit[0] : undefined;
          if (glow) w.__vexaNamedAudioData(index, glow, Array.from(pcm), Date.now());
          else w.__vexaPerSpeakerAudioData(index, Array.from(pcm), Date.now());
        },
      });
      await w.__vexaGmeetCapture.start();
      await w.__vexaRemoteAudioReady?.();
    }
  }, { isMixed: mixed, isJitsi: jitsi, isTeams: inv.platform === 'teams', isZoom: inv.platform === 'zoom', botName: inv.botName,
      // How long the Teams lane waits for the server mix before capturing every track instead.
      mainAudioGraceMs: Number(process.env.VEXA_TEAMS_MAIN_AUDIO_GRACE_MS || 15000),
      // How long a PICKED mix may stay wholly silent before the lane abandons it for every track.
      mainAudioSilenceMs: Number(process.env.VEXA_TEAMS_MAIN_AUDIO_SILENCE_MS || 20000),
      mainAudioEnergyRms: Number(process.env.VEXA_TEAMS_MAIN_AUDIO_ENERGY_RMS || 0.006) }).catch((e: any) => {
    console.error(`[bot] capture bridge: page-side start failed: ${String(e)}`); // L4: surfaces only on the VM
  });

  // Captions are OFF by default now: the bot does not touch the meeting's UI to get a name source
  // it was measured not to need (see TEAMS_ENABLE_CAPTIONS). Opt back in with
  // VEXA_TEAMS_ENABLE_CAPTIONS=1; the reader still consumes captions a meeting already has on.
  if (inv.platform === 'teams' && TEAMS_ENABLE_CAPTIONS) {
    void runTeamsCaptionEnable(page, undefined, undefined, undefined, onObservation)
      .catch(() => { /* the helper already swallows + observes */ });
  }

  // Stop fn: tear the page-side capture down on teardown (best-effort; the page may be closing).
  return async () => {
    if (countersTimer) clearInterval(countersTimer);
    activity?.unavailable();
    await captureTarget.evaluate(() => {
      const w = (globalThis as any) as Record<string, any>;
      try { w.__vexaGmeetCapture?.stop?.(); } catch { /* best-effort */ }
      try { if (w.__vexaTeamsHealthTimer) { (globalThis as any).clearInterval(w.__vexaTeamsHealthTimer); w.__vexaTeamsHealthTimer = null; } } catch { /* */ }
      try { w.__vexaTeamsSpeakers?.destroy?.(); w.__vexaTeamsSpeakers = null; } catch { /* best-effort */ }
      // destroy() flushes a caption still mid-refinement as stable:false — the meeting's last
      // words survive here or nowhere, which is why the flush lives in the stop path.
      try { w.__vexaTeamsCaptions?.destroy?.(); w.__vexaTeamsCaptions = null; } catch { /* best-effort */ }
      // destroy() flushes a deactivation for every source still active, so the meeting's last turn
      // closes in the tape instead of dangling open — the caption reader's flush, for the same
      // reason: the tail survives here or nowhere.
      try { w.__vexaCsrcPoll?.destroy?.(); w.__vexaCsrcPoll = null; } catch { /* best-effort */ }
      try { w.__vexaJitsiSpeakers?.destroy?.(); w.__vexaJitsiSpeakers = null; } catch { /* best-effort */ }
      try { w.__vexaJitsiChat?.destroy?.(); w.__vexaJitsiChat = null; } catch { /* best-effort */ }
      try { w.__vexaZoomSpeakers?.destroy?.(); w.__vexaZoomSpeakers = null; } catch { /* best-effort */ }
      try { if (w.__vexaMixRescan) { (globalThis as any).clearInterval(w.__vexaMixRescan); w.__vexaMixRescan = null; } } catch { /* */ }
      try { if (w.__vexaMixedCapture && typeof w.__vexaMixedCapture.stop === 'function') w.__vexaMixedCapture.stop(); } catch { /* best-effort */ }
      try { w.__vexaMixCtx?.close?.(); } catch { /* best-effort */ }
      try { w.__vexaGmeetSpeakers?.destroy?.(); } catch { /* best-effort */ }
    }).catch(() => { /* page already gone */ });
  };
}

/**
 * The one cheap repair the deaf-capture guard may attempt (#1192): drop the mixed-lane capture so
 * the rescan already running every 2s re-creates it over the SAME mix. Nothing is torn down that
 * the rescan does not rebuild — the AudioContext, the destination and the connected sources all
 * stay — and a successful restart calls `__vexaRemoteAudioReady` again, which re-arms the silence
 * window from the moment the bot could hear again.
 *
 * Best-effort and side-effect-free where it does not apply: the gmeet lane has no rescan, and a
 * closing page just rejects the evaluate. Returns whether a restart was actually requested.
 */
export async function restartMixedCapture(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    const w = (globalThis as any) as Record<string, any>;
    if (!w.__vexaMixRescan) return false;   // not the mixed lane (or the bridge is already torn down)
    try {
      if (w.__vexaMixedCapture && typeof w.__vexaMixedCapture.stop === 'function') w.__vexaMixedCapture.stop();
    } catch { /* best-effort: the guard re-runs if this did not take */ }
    w.__vexaMixedCapture = null;            // the rescan re-creates it (and re-signals ready)
    w.logBot?.('[mixed] capture restart requested (deaf-capture guard)');
    return true;
  }).catch(() => false);
}

/**
 * Start the page-side recording tap → recording.v1 chunks → the BotRecordingSink.  // L4 (O6/VM).
 *
 * The MediaRecorder loop lives in @vexa/record-chunker (bundled into window.VexaBrowserUtils, like
 * the capture bricks). It records the meeting's combined audio mix, base64-encodes each timeslice,
 * and hands it to `onChunk`. We bridge those chunks over the Playwright boundary to `recording.chunk`
 * using the SAME key the orchestrator closes with (`platform/native`); the sink uploads each chunk to
 * meeting-api the moment it arrives (#491/#412 — every finished part is durable before the meeting
 * ends), and the master is assembled server-side on read. The trailing empty is_final chunk (on
 * stop) is the COMPLETED signal. Started post-admission (on the live meeting page, where the
 * participant <audio> elements exist), exactly like the capture bridge.
 */
export async function startRecording(page: Page, inv: Invocation, recording: BotRecordingSink): Promise<() => Promise<void>> {
  const key = `${inv.platform}/${inv.nativeMeetingId ?? inv.connectionId ?? 'session'}`;
  // See startCaptureBridge: JaaS/8x8.vc-backed Jitsi (Brave Talk etc.) renders the participant
  // <audio>/<video> elements inside a cross-origin <iframe> — the recording tap must scan that
  // frame, not the top page. Every other platform keeps targeting `page` exactly as before.
  const captureTarget: any = inv.platform === 'jitsi' ? await resolveConferenceFrame(page) : page;
  // Recording part interval (ms): the MediaRecorder timeslice = the durable-upload granularity.
  // Env-overridable (VEXA_RECORDING_TIMESLICE_MS) so a live multi-part run can shrink it to land
  // ≥2 parts in a short meeting (#509 A5); default 15000 (production parity). Each timeslice is a
  // chunk uploaded the moment it is produced (recording.ts sink), so a SIGKILL leaves every
  // finished part durable (#412). Invalid / non-positive values fall back to the default.
  const timesliceMs = ((): number => {
    const raw = process.env.VEXA_RECORDING_TIMESLICE_MS;
    const n = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 15000;
  })();
  // Node-side: decode one base64 recording.v1 chunk → the per-chunk upload sink. mimeType→format.
  await page.exposeFunction('__vexaRecordingChunk', (base64: string, chunkSeq: number, isFinal: boolean, mimeType: string): void => {
    const bytes = base64 ? new Uint8Array(Buffer.from(base64, 'base64')) : new Uint8Array(0);
    const format: RecordingMasterFormat = /wav/i.test(mimeType) ? 'wav' : 'webm';
    recording.chunk(key, chunkSeq, isFinal, format, bytes);
  }).catch((e: Error) => { if (!String(e.message).includes('already registered')) throw e; });

  // Page-side: start the generic recording tap (finds + combines the page audio elements).
  await captureTarget.evaluate(async (timesliceMs: number) => {
    const w = (globalThis as any) as Record<string, any>;
    if (w.VexaBrowserUtils?.createRecordingTap && !w.__vexaRecordingTap) {
      w.__vexaRecordingTap = w.VexaBrowserUtils.createRecordingTap({
        timesliceMs,
        onChunk: async (c: { base64: string; chunkSeq: number; isFinal: boolean; mimeType: string }) => {
          try { await w.__vexaRecordingChunk(c.base64, c.chunkSeq, c.isFinal, c.mimeType); return true; }
          catch { return false; }
        },
      });
      await w.__vexaRecordingTap.start();
    }
  }, timesliceMs).catch((e: any) => { console.error(`[bot] recording bridge: page-side start failed: ${String(e)}`); });

  // Stop fn: stop the recorder so it flushes the final (isFinal) chunk → master assembly.
  return async () => {
    await captureTarget.evaluate(async () => {
      const w = (globalThis as any) as Record<string, any>;
      try { await w.__vexaRecordingTap?.stop?.(); } catch { /* best-effort */ }
    }).catch(() => { /* page already gone */ });
  };
}

/**
 * The SPEAK path — inject TTS audio into the bot's mic.  // L4 (O6/VM): live-validated.
 *
 * Production (services/vexa-bot/core/src/index.ts:595, 1039–1059 + services/tts-playback.ts)
 * does this at the OS level, not via a page fake-mic: a PulseAudio chain `tts_sink → virtual_mic`
 * is what Chromium captures as its microphone. The bot (a) unmutes the meeting-UI mic button
 * (page.evaluate clicks the platform's mic control), (b) writes synthesized PCM to the tts_sink
 * device (paplay) which feeds virtual_mic, then (c) re-mutes after a short tail.
 *
 * This bot package does not own the PulseAudio/TTS process plumbing (that is the container
 * entrypoint + a TTS service, outside the bot's import surface), so here we wire only the
 * BROWSER half it CAN drive — the meeting-UI mic toggle — and leave a clearly-marked seam for
 * the OS-level audio injection the VM image provides. Speaking is gated on inv.voiceAgentEnabled.
 */
export interface SpeakController {
  /** Begin speaking `text` (TTS synthesized + injected via the VM's PulseAudio chain). */
  speak(text: string, voice?: string): Promise<void>;
  /** Stop any in-flight speech (barge-in). */
  stop(): Promise<void>;
}

export function createSpeakController(page: Page, inv: Invocation): SpeakController {
  const enabled = !!inv.voiceAgentEnabled;
  const platform = inv.platform;
  const tts = createTtsPlayback((m) => console.log(`[bot] ${m}`));   // OS-level TTS→tts_sink half

  // Toggle the meeting-UI mic button so the bot is audible only while speaking (production
  // unmutes before speech + auto-mutes after — index.ts:1039–1059). The PulseAudio source
  // (tts_sink → virtual_mic) is the actual audio path and is provided by the VM image.
  const setMic = async (on: boolean): Promise<void> => {
    // JaaS/8x8.vc-backed Jitsi (Brave Talk etc.): the mic button lives in the embedding <iframe>,
    // not the top page — resolve it fresh each call (cheap; the toggle isn't in a hot loop).
    const target: any = platform === 'jitsi' ? await resolveConferenceFrame(page) : page;
    // Runs IN THE BROWSER; reach the DOM via globalThis (no DOM types in this Node-typed file).
    await target.evaluate(({ on, platform }: any) => {
      const doc = (globalThis as any).document;
      const click = (sel: string) => doc?.querySelector(sel)?.click();
      if (platform === 'teams') click('#microphone-button');
      else if (platform === 'zoom') click('.join-audio-container__btn');
      else {
        // Google Meet / Jitsi: the mic toggle is identified by its aria-label —
        // "microphone" on Meet, "Toggle mute audio" on stock jitsi builds.
        const btn = Array.from(doc?.querySelectorAll('[role="button"],button') ?? [])
          .find((b: any) => /microphone|mute audio/i.test(b.getAttribute('aria-label') ?? '')) as any;
        btn?.click();
      }
      void on; // toggle is a click; on/off intent is logged by the caller
    }, { on, platform }).catch(() => { /* L4: best-effort UI drive */ });
  };

  return {
    async speak(text: string, voice?: string): Promise<void> {
      if (!enabled) { console.error('[bot] speak ignored: voiceAgentEnabled is false'); return; }
      console.log(`[bot] speak: "${text.slice(0, 60)}"`);
      await setMic(true);                                     // (a) unmute the meeting-UI mic button
      // (b) synthesize via the TTS service + stream PCM to tts_sink → virtual_mic (the bot's mic).
      await tts.speak(text, voice).catch((e) => console.error(`[bot] speak: tts failed: ${String(e)}`));
      await setMic(false);                                    // (c) re-mute after the tail
    },
    async stop(): Promise<void> {
      if (!enabled) return;
      tts.stop();                                             // barge-in: kill playback + re-mute tts_sink
      await setMic(false);
      console.log('[bot] speak_stop');
    },
  };
}
