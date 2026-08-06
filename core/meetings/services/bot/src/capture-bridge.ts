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
import { getJoinBrowserArgs } from '@vexa/join';
import type { RecordingMasterFormat } from '@vexa/recording';
import { isMixedLanePlatform, isPerTrackLanePlatform, type Invocation } from './config.js';
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
  const perTrack = isPerTrackLanePlatform(inv.platform);   // Zoom: per-track through the per-channel lane
  const useMix = mixed && !perTrack;                        // Teams/Jitsi: the pyannote mixed lane
  const jitsi = inv.platform === 'jitsi';
  const lane: 'gmeet' | 'mixed' = mixed ? 'mixed' : 'gmeet';

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
    // Teams/Jitsi (useMix): one combined stream → the pyannote mixed lane. Zoom + gmeet: per-channel —
    // an unbound track (name not yet resolved) arrives with no name → the per-channel lane opens the
    // turn UNKNOWN and upgrades it the moment the resolver binds (gmeet-pipeline onset-adopt); the
    // named path is __vexaNamedAudioData.
    if (useMix) pipeline.feedMixedAudio(pcm, ts);
    else pipeline.feedAudio(speakerIndex, undefined, pcm, ts);
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
  // C1: the four hint hops on one periodic, cumulative counter line —
  // page-emitted lives in the page console ([TeamsSpeakers]/[JitsiSpeakers] logs);
  // bridge-crossed / pipeline-received / binder matched|missed are Node-side.
  // Only the pyannote mixed lane exposes hintCounters (the binder's hop tally). The per-channel
  // lane names tracks page-side (the resolver), so there is no binder to count — skip the line.
  const countersTimer = pipeline.hintCounters ? setInterval(() => {
    const c = pipeline.hintCounters;
    console.log(`[bot] hint-counters bridge-crossed=${hintsBridgeCrossed()} pipeline-received=${c?.received ?? 0} binder-matched=${c?.matched ?? 0} binder-missed=${c?.missed ?? 0}`);
  }, 30_000) : null;
  countersTimer?.unref?.();   // observability only — never holds the process open

  await page.exposeFunction('__vexaPerSpeakerAudioData', onPerSpeakerAudio).catch((e: Error) => {
    if (!String(e.message).includes('already registered')) throw e;
  });
  await page.exposeFunction('__vexaNamedAudioData', onNamedAudio).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaSpeakerHint', onSpeakerHint).catch(() => { /* optional */ });
  await page.exposeFunction('__vexaRemoteAudioReady', (): void => activity?.ready()).catch((e: Error) => {
    if (!String(e.message).includes('already registered')) throw e;
  });
  // jitsi chat → the embedder's sink (a transcript.v1 `chat` segment at the composition root).
  await page.exposeFunction('__vexaChatMessage', (sender: string, text: string): void => {
    try { onChat?.(sender, text); } catch (e) { console.error(`[bot] chat sink rejected: ${String(e)}`); }
  }).catch(() => { /* optional */ });

  // ── Start the page-side capture (VexaBrowserUtils preferred; production inline fallback). ──
  // The body of this callback runs IN THE BROWSER (Playwright serializes it); DOM globals are
  // reached via globalThis (this file type-checks against the Node lib — no DOM types here).
  await page.evaluate(async ({ isMixed, isPerTrack, isJitsi, isTeams, isZoom, botName }) => {
    const w = (globalThis as any) as Record<string, any>;

    // ── The track→name VOTE resolver (SHARED by the Zoom per-track lane AND the gmeet lane) ──────────
    // Both lanes are the SAME problem: anonymous per-participant audio channels + a decoupled, flicker-
    // prone single-active-speaker glow (Zoom's DOM spotlight; gmeet's per-tile speaking indicator — its
    // <audio> elements carry no participant name, per probeDom). Name each stable channel by VOTING: every
    // clean moment (this channel is the loudest hot one + exactly one speaker lit) casts a channel<->name
    // vote; the binding is the argmax. A wrong early sample (a glow flicker naming Jacob during Sue's turn)
    // is ONE vote the true speaker outweighs — self-correcting. MARGIN hysteresis stops churn; 1:1-by-
    // identity stops the flicker pasting one speaker's name onto another's channel; high-PURITY co-hold
    // still allows two genuinely same-named people; a name frees on IDLE_RELEASE (leave / reconnect).
    // Floor: a channel still needs the glow to name it correctly sometimes; a speaker never lit stays
    // separated but unnamed. dom-active platforms (Zoom/Jitsi/gmeet) are 'exclusive'; Teams 'additive'.
    const makeTrackNamer = (mode: 'additive' | 'exclusive'): any => ({
      mode,
      speaking: new Map<string, number>(),                 // active-speaker name → since (ms)
      hot: new Map<number, { ts: number; e: number }>(),   // channel → last-energetic {ts, peak}
      votes: new Map<number, Map<string, number>>(),       // channel → name → co-occurrence tally
      names: new Map<number, string>(),                    // channel → committed name (= argmax vote)
      HOT_MS: 600, MARGIN: 3, IDLE_RELEASE_MS: 8000, PURITY: 0.7,
      onSpeak(name: string | null, tMs: number, isEnd: boolean): void {
        if (!name) return;
        if (isEnd) { this.speaking.delete(name); return; }
        if (this.mode === 'exclusive') { this.speaking.clear(); this.speaking.set(name, tMs); }
        else if (!this.speaking.has(name)) this.speaking.set(name, tMs);
      },
      markHot(ch: number, ts: number, e: number): void { this.hot.set(ch, { ts, e }); },
      resolve(ch: number, ts: number): string | undefined {
        // VOTE when THIS channel is the loudest hot one while exactly one speaker is lit.
        let loud = -1, loudE = -1;
        for (const [c, h] of this.hot) { if (ts - h.ts < this.HOT_MS && h.e > loudE) { loudE = h.e; loud = c; } }
        const active = Array.from(this.speaking.keys()) as string[];
        if (loud === ch && active.length === 1) {
          const n = active[0];
          let v = this.votes.get(ch); if (!v) { v = new Map(); this.votes.set(ch, v); }
          v.set(n, (v.get(n) || 0) + 1);
          this.rederive(ch, ts);
        }
        return this.names.get(ch);   // committed binding (undefined until the first confident bind)
      },
      rederive(ch: number, ts: number): void {
        const v = this.votes.get(ch); if (!v) return;
        let total = 0; for (const c of v.values()) total += c;
        // 1:1 BY IDENTITY: a name committed to another still-active channel is that stream's identity and
        // unavailable, no matter how many stray glow-flicker votes it collected here — UNLESS this channel's
        // votes are high-PURITY (a genuine second same-named speaker). A name frees once its owner is idle.
        const available = (name: string): boolean => {
          let activeOwner = false;
          for (const [c, n] of this.names) {
            if (n !== name || c === ch) continue;
            const h = this.hot.get(c);
            if (h && ts - h.ts <= this.IDLE_RELEASE_MS) { activeOwner = true; break; }
          }
          if (!activeOwner) return true;
          const vn = v.get(name) || 0;
          return vn >= this.PURITY * total && vn >= this.MARGIN;
        };
        let best = '', bestN = -1;
        for (const [n, c] of v) if (c > bestN && available(n)) { bestN = c; best = n; }
        if (best === '') return;
        const cur = this.names.get(ch);
        if (best === cur) return;
        const curN = cur ? (v.get(cur) || 0) : -1;
        if (bestN < curN + this.MARGIN) return;   // hysteresis: real evidence, not glow churn
        for (const [c, n] of this.names) {
          if (n !== best || c === ch) continue;
          const h = this.hot.get(c);
          if (!h || ts - h.ts > this.IDLE_RELEASE_MS) this.names.delete(c);   // release idle owner only
        }
        this.names.set(ch, best);
        w.logBot?.('[pertrack] bound ch=' + ch + ' → ' + best + ' (' + bestN + ' votes)');
      },
    });

    if (isMixed) {
      // Zoom/Teams/Jitsi ride the WebRTC hook (installRemoteAudioHook, installed pre-nav), which mirrors
      // each remote participant's audio track into w.__vexaCapturedRemoteAudioStreams AND into a hidden
      // <audio data-vexa-injected> element (that latter copy is what the recorder taps — untouched by
      // either path below). Two transcription topologies split here:
      //   • PER-TRACK (Zoom — confirmed live: multi-stream, stable per-participant, 0 teardowns): capture
      //     EACH track on its OWN channel and name it from the active-speaker hints, through the SAME
      //     per-channel, name-at-onset engine Google Meet uses. A track = one speaker (ground truth), so
      //     overlap is separated by the tracks themselves.
      //   • MIXED (Teams/Jitsi — per-track topology NOT yet witnessed; Teams may use remapped active-
      //     speaker SLOTS): combine every track into ONE stream and let @vexa/mixed-pipeline (pyannote)
      //     re-separate speakers, named by time-windowed hints. Kept until each platform's streams are
      //     seen live (streams ≈ participants → safe to flip to per-track; streams ≫ participants → slots).
      if (isPerTrack) {
      // ── The track→name resolver (shared makeTrackNamer, defined above) ──
      // Zoom's per-participant WebRTC streams are stable but anonymous; the DOM lights ONE dominant
      // speaker at a time (exclusive) — worse under screen-share. Teams voice-outline can light several
      // (additive). The vote resolver attaches each stable channel to the right name and defends it.
      if (!w.__vexaTrackNamer) w.__vexaTrackNamer = makeTrackNamer(isTeams ? 'additive' : 'exclusive');

      // ── Per-track capture: one 16 kHz PCM tap per remote track, each on its own stable channel ──
      // ONE shared AudioContext hosts every track's tap (Chromium hard-caps concurrent AudioContexts
      // at 6 — a per-track context would drop the 7th+ participant in a large meeting). Each track gets
      // its own ScriptProcessor on that context; the bot page is headless with no UI to stutter, so the
      // many-node cost that retired ScriptProcessor on the user's busy meeting page does not apply here.
      // The accumulated-audio-time clock (anchor + samples/rate, the SAME the mix path proved) stamps
      // every frame on the page clock = the hints' clock, so the resolver can correlate energy with the
      // active-speaker signal and the per-channel lane times turns correctly.
      const setupPerTrack = (): void => {
        const streams = (w.__vexaCapturedRemoteAudioStreams || []) as Array<{ id: string }>;
        if (!streams.length) return;
        if (!w.__vexaTrackCtx) {
          w.__vexaTrackCtx = new (globalThis as any).AudioContext({ sampleRate: 16000 });
          w.__vexaTrackCtx.resume?.();
          w.__vexaTrackCaps = new Map();
          w.__vexaTrackNextCh = 0;
        }
        const ctx = w.__vexaTrackCtx;
        const SR = 16000, SILENCE = 0.005;
        for (const s of streams) {
          if (!s || w.__vexaTrackCaps.has(s.id)) continue;
          const ch: number = w.__vexaTrackNextCh++;
          try {
            const src = ctx.createMediaStreamSource(s);
            const proc = ctx.createScriptProcessor(4096, 1, 1);
            const startMs = Date.now();
            let processed = 0;
            proc.onaudioprocess = (e: any): void => {
              const input = e.inputBuffer.getChannelData(0) as Float32Array;
              const ts = startMs + (processed / SR) * 1000;   // wall-clock of this frame's first sample
              processed += input.length;                       // count ALL samples (silent too) → no drift
              let maxVal = 0;
              for (let i = 0; i < input.length; i++) { const a = Math.abs(input[i]); if (a > maxVal) maxVal = a; }
              if (maxVal <= SILENCE) return;                   // gate silence (as the mix path did)
              w.__vexaTrackNamer.markHot(ch, ts, maxVal);      // peak energy → the dominant-slot ranking
              const name = w.__vexaTrackNamer.resolve(ch, ts);
              const arr = Array.from(input);                   // copy — the input buffer is reused
              if (name) w.__vexaNamedAudioData(ch, name, arr, ts);
              else w.__vexaPerSpeakerAudioData(ch, arr, ts);
            };
            src.connect(proc);
            proc.connect(ctx.destination);                     // pull the processor (it outputs silence)
            w.__vexaTrackCaps.set(s.id, { ch, src, proc });
            w.logBot?.('[pertrack] capturing ch=' + ch + ' (' + w.__vexaTrackCaps.size + ' track(s))');
            w.__vexaRemoteAudioReady?.();
          } catch (e: any) { w.logBot?.('[pertrack] track setup failed ch=' + ch + ': ' + String(e)); }
        }
      };
      setupPerTrack();
      w.__vexaMixRescan = (globalThis as any).setInterval(setupPerTrack, 2000); // pick up late-joining tracks
      // Zoom's active-speaker DOM watcher — the WHO signal the resolver correlates with per-track energy
      // (also teed to __vexaSpeakerHint for telemetry; the pipeline's recordHint is a no-op on this lane).
      if (isZoom && w.VexaBrowserUtils?.createZoomSpeakers && !w.__vexaZoomSpeakers) {
        let lastActive: string | null = null;
        w.__vexaZoomSpeakers = w.VexaBrowserUtils.createZoomSpeakers({
          selfName: botName,
          log: (m: string) => w.logBot?.('[ZoomSpeakers] ' + m),
          onSpeakerChange: (name: string | null) => {
            const tMs = Date.now();
            if (name) { w.__vexaTrackNamer?.onSpeak(name, tMs, false); w.__vexaSpeakerHint?.(name, tMs, false); }
            else if (lastActive) { w.__vexaTrackNamer?.onSpeak(lastActive, tMs, true); w.__vexaSpeakerHint?.(lastActive, tMs, true); }
            lastActive = name;
          },
        });
      }
      return;
      }
      // ── MIXED lane (Teams/Jitsi): one combined stream + active-speaker hints, pyannote naming ──
      // Teams delivers the COMPLETE meeting audio as a single server-side mix whose track id is prefixed
      // "mainAudio" — witnessed live: the standard web client receives exactly ONE audio receiver. The
      // bot is ALSO handed a redundant track (e.g. a dominant-speaker copy) whose audio is already inside
      // that mix; combining both double-feeds every word to the transcriber → repeated words. So on Teams
      // mix ONLY the mainAudio track. Jitsi keeps combining all tracks (its topology isn't witnessed).
      const setupMix = (): void => {
        let streams = (w.__vexaCapturedRemoteAudioStreams || []) as Array<any>;
        if (isTeams && streams.length) {
          const mainAudio = streams.filter((s: any) => (s.getAudioTracks?.() || []).some((t: any) => String(t.id || '').toLowerCase().startsWith('mainaudio')));
          if (!mainAudio.length && !w.__vexaTeamsNoMainWarned) {
            w.__vexaTeamsNoMainWarned = true;
            w.logBot?.('[mixed] Teams: no "mainAudio" track among ' + streams.length + ' stream(s) yet — waiting for the mix (never combine 2 → avoids doubling)');
          }
          streams = mainAudio;   // Teams: the server mix ALONE; combining a 2nd stream double-transcribes every word
        }
        if (!streams.length) return;
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
            w.logBot?.('[mixed] connected remote stream ' + w.__vexaMixSeen.size);
          } catch { /* a stream may not be connectable yet */ }
        }
        if (!w.__vexaMixedCapture && w.__vexaMixSeen.size && w.VexaBrowserUtils?.createMixedAudioCapture) {
          w.__vexaMixedCapture = true; // guard re-entry while the async create resolves
          // Accumulated-audio-time clock (anchor + samples/rate on the page clock = the hints' clock) —
          // NOT Node receipt time nor Date.now() at callback (which still carries the ~256ms buffer lag);
          // without it ~3/4 of hints missed their binder window → misattribution.
          Promise.resolve(w.VexaBrowserUtils.createMixedAudioCapture(w.__vexaMixDest.stream, (pcm: Float32Array, tsMs?: number) => w.__vexaPerSpeakerAudioData(0, Array.from(pcm), tsMs)))
            .then((cap: any) => { w.__vexaMixedCapture = cap; return cap?.start?.(); })
            .then(async () => {
              await w.__vexaRemoteAudioReady?.();
              w.logBot?.('[mixed] capture started over ' + w.__vexaMixSeen.size + ' stream(s)');
            })
            .catch((e: any) => { w.__vexaMixedCapture = null; w.logBot?.('[mixed] capture start failed: ' + String(e)); });
        }
      };
      setupMix();
      w.__vexaMixRescan = (globalThis as any).setInterval(setupMix, 2000); // pick up late-arriving tracks
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
          });
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
      // (Zoom's watcher lives in the per-track branch above — it feeds the resolver, not the mix.)
      return;
    }
    // gmeet lane: per-channel capture, named through the SAME vote resolver as Zoom. gmeet is the same
    // shape — anonymous per-participant <audio> streams (each a stable channel index) + a decoupled per-
    // tile speaking glow (probeDom confirmed the audio elements carry NO participant name). The old code
    // stamped the RAW instantaneous glow, which flickers at turn onset (Sue's words → Jacob). Voting each
    // element to its participant over time defends against that flicker. The glow lights one tile at a
    // time → 'exclusive'.
    if (w.VexaBrowserUtils?.createGmeetCapture && !w.__vexaGmeetCapture) {
      if (!w.__vexaTrackNamer) w.__vexaTrackNamer = makeTrackNamer('exclusive');
      w.__vexaGmeetSpeakers = w.__vexaGmeetSpeakers
        ?? w.VexaBrowserUtils.createGmeetSpeakers?.({ log: (m: string) => w.logBot?.('[PerSpeaker] ' + m) });
      w.__vexaGmeetCapture = w.VexaBrowserUtils.createGmeetCapture({
        log: (m: string) => w.logBot?.('[PerSpeaker] ' + m),
        onAudio: (index: number, pcm: Float32Array) => {
          w.__vexaGmeetSpeakers?.reportTrackAudio?.(index);
          const ts = Date.now();
          // The single lit tile is the current active speaker → feed it once per change; this element's
          // peak energy marks it hot. resolve(index) is the VOTED name (stable, flicker-proof) — not the
          // instantaneous glow. Unbound (early frames) → UNKNOWN, upgraded by the pipeline once it binds.
          const lit: string[] = w.__vexaGmeetSpeakers?.litNames?.() ?? [];
          const glow = lit.length === 1 ? lit[0] : undefined;
          if (glow && w.__vexaGmeetGlow !== glow) { w.__vexaTrackNamer.onSpeak(glow, ts, false); w.__vexaGmeetGlow = glow; }
          let maxVal = 0;
          for (let i = 0; i < pcm.length; i++) { const a = Math.abs(pcm[i]); if (a > maxVal) maxVal = a; }
          w.__vexaTrackNamer.markHot(index, ts, maxVal);
          const name = w.__vexaTrackNamer.resolve(index, ts);
          if (name) w.__vexaNamedAudioData(index, name, Array.from(pcm), ts);
          else w.__vexaPerSpeakerAudioData(index, Array.from(pcm), ts);
        },
      });
      await w.__vexaGmeetCapture.start();
      await w.__vexaRemoteAudioReady?.();
      // STRUCTURAL-NAMING PROBE (temporary): dump probeDom() a handful of times so we can see whether
      // each participant's <audio> element sits inside a tile carrying data-participant-id + name. If it
      // does, gmeet naming can be a DIRECT structural read (audio→tile→name) instead of the flicker-prone
      // instantaneous glow that misattributes at turn onset (Sue's words → Jacob). Logged as [gmeet-probe];
      // remove once the naming design is settled.
      if (w.__vexaGmeetSpeakers?.probeDom && w.__vexaGmeetProbeCount === undefined) {
        w.__vexaGmeetProbeCount = 0;
        const probe = (): void => {
          try { w.logBot?.('[gmeet-probe] ' + JSON.stringify(w.__vexaGmeetSpeakers.probeDom())); }
          catch (e: any) { w.logBot?.('[gmeet-probe] failed: ' + String(e)); }
          if (++w.__vexaGmeetProbeCount >= 6 && w.__vexaGmeetProbeTimer) {
            (globalThis as any).clearInterval(w.__vexaGmeetProbeTimer); w.__vexaGmeetProbeTimer = null;
          }
        };
        probe();
        w.__vexaGmeetProbeTimer = (globalThis as any).setInterval(probe, 20000); // 6× over ~2 min → catch active speakers
      }
    }
  }, { isMixed: mixed, isPerTrack: perTrack, isJitsi: jitsi, isTeams: inv.platform === 'teams', isZoom: inv.platform === 'zoom', botName: inv.botName }).catch((e) => {
    console.error(`[bot] capture bridge: page-side start failed: ${String(e)}`); // L4: surfaces only on the VM
  });

  // Stop fn: tear the page-side capture down on teardown (best-effort; the page may be closing).
  return async () => {
    if (countersTimer) clearInterval(countersTimer);
    activity?.unavailable();
    await page.evaluate(() => {
      const w = (globalThis as any) as Record<string, any>;
      try { w.__vexaGmeetCapture?.stop?.(); } catch { /* best-effort */ }
      try { if (w.__vexaGmeetProbeTimer) { (globalThis as any).clearInterval(w.__vexaGmeetProbeTimer); w.__vexaGmeetProbeTimer = null; } } catch { /* */ }
      try { w.__vexaTeamsSpeakers?.destroy?.(); w.__vexaTeamsSpeakers = null; } catch { /* best-effort */ }
      try { w.__vexaJitsiSpeakers?.destroy?.(); w.__vexaJitsiSpeakers = null; } catch { /* best-effort */ }
      try { w.__vexaJitsiChat?.destroy?.(); w.__vexaJitsiChat = null; } catch { /* best-effort */ }
      try { w.__vexaZoomSpeakers?.destroy?.(); w.__vexaZoomSpeakers = null; } catch { /* best-effort */ }
      try { if (w.__vexaMixRescan) { (globalThis as any).clearInterval(w.__vexaMixRescan); w.__vexaMixRescan = null; } } catch { /* */ }
      try {
        if (w.__vexaTrackCaps) {
          for (const e of w.__vexaTrackCaps.values()) {
            try { if (e?.proc) { e.proc.disconnect(); e.proc.onaudioprocess = null; } e?.src?.disconnect(); } catch { /* */ }
          }
          w.__vexaTrackCaps = null;
        }
      } catch { /* best-effort */ }
      try { w.__vexaTrackCtx?.close?.(); w.__vexaTrackCtx = null; } catch { /* best-effort */ }
      try { if (w.__vexaMixedCapture && typeof w.__vexaMixedCapture.stop === 'function') w.__vexaMixedCapture.stop(); } catch { /* best-effort */ }
      try { w.__vexaMixCtx?.close?.(); } catch { /* best-effort */ }
      try { w.__vexaGmeetSpeakers?.destroy?.(); } catch { /* best-effort */ }
    }).catch(() => { /* page already gone */ });
  };
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
  await page.evaluate(async (timesliceMs) => {
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
  }, timesliceMs).catch((e) => { console.error(`[bot] recording bridge: page-side start failed: ${String(e)}`); });

  // Stop fn: stop the recorder so it flushes the final (isFinal) chunk → master assembly.
  return async () => {
    await page.evaluate(async () => {
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
    // Runs IN THE BROWSER; reach the DOM via globalThis (no DOM types in this Node-typed file).
    await page.evaluate(({ on, platform }) => {
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
