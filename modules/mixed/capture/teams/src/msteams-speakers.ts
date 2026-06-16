/**
 * MS Teams speaker attribution ("blue squares") — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright, no cross-file imports — the bot
 * bundles this file standalone). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js; msteams/recording.ts's
 *    page.evaluate instantiates it instead of inlining the detector.
 *  - the extension: imported by inpage.ts on Teams hosts; hints label the
 *    mixed tabCapture track.
 *
 * Signal: `[data-tid="voice-level-stream-outline"]` presence (the tile emits
 * a voice-level signal) + `vdi-frame-occlusion` class on it or an ancestor
 * (= actively speaking). NO caption dependency — captions may be disabled.
 * Debounced speaking start/stop events per participant feed the
 * ChunkedTranscriber's name binder as 'dom-outline' hints.
 *
 * This module OWNS the Teams speaker-detection selectors (single source —
 * platforms/msteams/selectors.ts re-exports from here).
 */

export const teamsParticipantSelectors: string[] = [
  '[data-tid*="participant"]',
  '[aria-label*="participant"]',
  '[data-tid*="roster"]',
  '[data-tid*="roster-item"]',
  '[data-tid*="video-tile"]',
  '[data-tid*="videoTile"]',
  '[data-tid*="participant-tile"]',
  '[data-tid*="participantTile"]',
  '[role="listitem"]',
  '.participant-tile',
  '.video-tile',
  '.roster-item',
];

export const teamsNameSelectors: string[] = [
  // Look for the actual name div structure
  'div[class*="___2u340f0"]', // The actual name div class pattern
  '[data-tid*="display-name"]',
  '[data-tid*="participant-name"]',
  '[data-tid*="user-name"]',
  '[aria-label*="name"]',
  '.participant-name',
  '.display-name',
  '.user-name',
  '.roster-item-name',
  '.video-tile-name',
  'span[title]',
];

export const teamsParticipantIdSelectors: string[] = [
  '[data-tid]',
  '[data-participant-id]',
  '[data-user-id]',
];

export const teamsMeetingContainerSelectors: string[] = [
  '[role="main"]',
  'body',
];

const VOICE_LEVEL_SELECTOR = '[data-tid="voice-level-stream-outline"]';

export interface TeamsSpeakerIdentity {
  id: string;
  name: string;
}

export interface TeamsSpeakersOptions {
  /** Local participant / bot display name — its tiles are never reported. */
  selfName?: string;
  /** Debounced speaking state change: isEnd=false → started speaking,
   *  isEnd=true → stopped. tMs = wall-clock at emit. */
  onSpeaking: (name: string, id: string, isEnd: boolean, tMs: number) => void;
  log?: (msg: string) => void;
  /** Debounce for state-change emission (ms). Default 300 — matches the bot. */
  debounceMs?: number;
}

export interface TeamsSpeakers {
  /** Names currently in 'speaking' state. */
  getSpeaking(): string[];
  destroy(): void;
}

type SpeakingState = 'speaking' | 'silent' | 'unknown';

export function createTeamsSpeakers(opts: TeamsSpeakersOptions): TeamsSpeakers {
  const log = opts.log || (() => { /* silent */ });
  const selfLower = (opts.selfName || '').toLowerCase();
  const debounceMs = opts.debounceMs ?? 300;

  interface Identity { id: string; name: string; element: HTMLElement }

  // ── Participant identity cache ──
  const cache = new Map<HTMLElement, Identity>();

  function extractId(element: HTMLElement): string {
    let id = element.getAttribute('data-acc-element-id') ||
      element.getAttribute('data-tid') ||
      element.getAttribute('data-participant-id') ||
      element.getAttribute('data-user-id') ||
      element.getAttribute('data-object-id') ||
      element.getAttribute('id');
    if (!id) {
      const stableChild = element.querySelector(teamsParticipantIdSelectors.join(', '));
      if (stableChild) {
        id = stableChild.getAttribute('data-tid') ||
          stableChild.getAttribute('data-participant-id') ||
          stableChild.getAttribute('data-user-id');
      }
    }
    if (!id) {
      if (!(element as any).dataset.vexaGeneratedId) {
        (element as any).dataset.vexaGeneratedId = 'teams-id-' + Math.random().toString(36).substr(2, 9);
      }
      id = (element as any).dataset.vexaGeneratedId as string;
    }
    return id!;
  }

  function extractName(element: HTMLElement): string {
    const forbidden = [
      'more_vert', 'mic_off', 'mic', 'videocam', 'videocam_off',
      'present_to_all', 'devices', 'speaker', 'speakers', 'microphone',
      'camera', 'camera_off', 'share', 'chat', 'participant', 'user',
    ];
    for (const selector of teamsNameSelectors) {
      const nameElement = element.querySelector(selector) as HTMLElement | null;
      if (!nameElement) continue;
      let nameText = nameElement.textContent ||
        (nameElement as any).innerText ||
        nameElement.getAttribute('title') ||
        nameElement.getAttribute('aria-label');
      if (!nameText || !nameText.trim()) continue;
      nameText = nameText.trim();
      if (forbidden.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) continue;
      if (nameText.length > 1 && nameText.length < 50) return nameText;
    }
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.includes('name')) {
      const m = ariaLabel.match(/name[:\s]+([^,]+)/i);
      if (m && m[1]) {
        const nameText = m[1].trim();
        if (nameText.length > 1 && nameText.length < 50) return nameText;
      }
    }
    return '';   // name not resolvable yet — emit NO hint rather than a meaningless GUID
  }

  function getIdentity(element: HTMLElement): Identity {
    let identity = cache.get(element);
    if (!identity) {
      identity = { id: extractId(element), name: extractName(element), element };
      cache.set(element, identity);
    } else if (!identity.name) {
      identity.name = extractName(element);   // the name div often renders after the tile
    }
    return identity;
  }

  // ── State machine (200ms hysteresis, signal-required) ──
  const states = new Map<string, { state: SpeakingState; hasSignal: boolean; lastChangeTime: number }>();
  const MIN_STATE_CHANGE_MS = 200;

  function updateState(id: string, r: { isSpeaking: boolean; hasSignal: boolean }): boolean {
    const current = states.get(id);
    const now = Date.now();
    if (!r.hasSignal) {
      if (current?.hasSignal) states.set(id, { state: 'unknown', hasSignal: false, lastChangeTime: now });
      return false;
    }
    const newState: SpeakingState = r.isSpeaking ? 'speaking' : 'silent';
    if (current?.state === newState && current?.hasSignal) return false;
    if (current && (now - current.lastChangeTime) < MIN_STATE_CHANGE_MS) return false;
    states.set(id, { state: newState, hasSignal: true, lastChangeTime: now });
    return true;
  }

  // ── Detection: voice-level outline + vdi-frame-occlusion ──
  function detectSpeakingState(element: HTMLElement): { isSpeaking: boolean; hasSignal: boolean } {
    const voiceOutline = element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
    if (!voiceOutline) return { isSpeaking: false, hasSignal: false };
    let current: HTMLElement | null = voiceOutline;
    while (current) {
      if (current.classList.contains('vdi-frame-occlusion')) return { isSpeaking: true, hasSignal: true };
      current = current.parentElement;
    }
    return { isSpeaking: false, hasSignal: true };
  }

  function hasRequiredSignal(element: HTMLElement): boolean {
    return element.querySelector(VOICE_LEVEL_SELECTOR) !== null;
  }

  // ── Debouncer ──
  const debounceTimers = new Map<string, number>();
  function debounce(key: string, fn: () => void): void {
    const t = debounceTimers.get(key);
    if (t !== undefined) clearTimeout(t);
    debounceTimers.set(key, setTimeout(() => { fn(); debounceTimers.delete(key); }, debounceMs) as unknown as number);
  }

  // ── Observer system ──
  const observers = new Map<HTMLElement, MutationObserver[]>();
  const rafHandles = new Map<string, number>();
  const speakingStates = new Map<string, SpeakingState>();
  let destroyed = false;

  function emit(state: SpeakingState, identity: Identity): void {
    if (state === 'unknown' || destroyed) return;
    if (!identity.name) return;   // unresolved name → don't emit a nameless/GUID hint
    if (selfLower && identity.name.toLowerCase().includes(selfLower)) return;
    log(`${state === 'speaking' ? '🎤' : '🔇'} [TeamsSpeakers] ${state === 'speaking' ? 'SPEAKER_START' : 'SPEAKER_END'}: ${identity.name} (${identity.id})`);
    opts.onSpeaking(identity.name, identity.id, state !== 'speaking', Date.now());
  }

  function checkAndEmit(identity: Identity): void {
    if (destroyed) return;
    if (!identity.element.isConnected) { removeParticipant(identity); return; }
    const r = detectSpeakingState(identity.element);
    if (updateState(identity.id, r) && r.hasSignal) {
      const newState: SpeakingState = r.isSpeaking ? 'speaking' : 'silent';
      speakingStates.set(identity.id, newState);
      debounce(identity.id, () => emit(newState, identity));
    }
  }

  function scheduleRAFCheck(identity: Identity): void {
    const check = () => {
      if (destroyed) return;
      if (!identity.element.isConnected) { removeParticipant(identity); return; }
      checkAndEmit(identity);
      rafHandles.set(identity.id, requestAnimationFrame(check));
    };
    rafHandles.set(identity.id, requestAnimationFrame(check));
  }

  function removeParticipant(identity: Identity): void {
    const t = debounceTimers.get(identity.id);
    if (t !== undefined) { clearTimeout(t); debounceTimers.delete(identity.id); }
    if (states.get(identity.id)?.state === 'speaking') emit('silent', identity);
    const obs = observers.get(identity.element);
    if (obs) { obs.forEach(o => o.disconnect()); observers.delete(identity.element); }
    const raf = rafHandles.get(identity.id);
    if (raf !== undefined) { cancelAnimationFrame(raf); rafHandles.delete(identity.id); }
    states.delete(identity.id);
    speakingStates.delete(identity.id);
    cache.delete(identity.element);
    delete (identity.element as any).dataset.vexaObserverAttached;
    log(`🗑️ [TeamsSpeakers] Removed: ${identity.name} (${identity.id})`);
  }

  function observeParticipant(element: HTMLElement): void {
    if ((element as any).dataset.vexaObserverAttached) return;
    if (!hasRequiredSignal(element)) return; // signal-only approach, no fallbacks
    const identity = getIdentity(element);
    (element as any).dataset.vexaObserverAttached = 'true';
    log(`👁️ [TeamsSpeakers] Observing: ${identity.name} (${identity.id})`);
    const voiceOutline = element.querySelector(VOICE_LEVEL_SELECTOR) as HTMLElement | null;
    if (!voiceOutline) return;
    const voiceObserver = new MutationObserver(() => checkAndEmit(identity));
    voiceObserver.observe(voiceOutline, { attributes: true, attributeFilter: ['style', 'class', 'aria-hidden'] });
    const containerObserver = new MutationObserver(() => {
      if (!hasRequiredSignal(element)) { removeParticipant(identity); return; }
      checkAndEmit(identity);
    });
    containerObserver.observe(element, { childList: true, subtree: true });
    observers.set(element, [voiceObserver, containerObserver]);
    scheduleRAFCheck(identity);
    checkAndEmit(identity);
  }

  function scanAndObserveAll(): void {
    const allSelectors = [...teamsParticipantSelectors, '[role="menuitem"]'];
    const seen = new WeakSet<HTMLElement>();
    let found = 0; let observed = 0;
    for (const selector of allSelectors) {
      document.querySelectorAll(selector).forEach(el => {
        if (el instanceof HTMLElement && !seen.has(el)) {
          seen.add(el);
          found++;
          if (hasRequiredSignal(el)) { observeParticipant(el); observed++; }
        }
      });
    }
    log(`🔍 [TeamsSpeakers] Scanned ${found} participants, observing ${observed} with signal`);
  }

  scanAndObserveAll();

  // Monitor for new/removed participants.
  const bodyObserver = new MutationObserver((mutationsList) => {
    if (destroyed) return;
    const allSelectors = [...teamsParticipantSelectors, '[role="menuitem"]'];
    for (const mutation of mutationsList) {
      if (mutation.type !== 'childList') continue;
      mutation.addedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        for (const selector of allSelectors) {
          if (el.matches(selector)) observeParticipant(el);
          el.querySelectorAll(selector).forEach(child => {
            if (child instanceof HTMLElement) observeParticipant(child);
          });
        }
      });
      mutation.removedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        for (const selector of teamsParticipantSelectors) {
          if (!el.matches(selector)) continue;
          const identity = cache.get(el);
          if (identity) removeParticipant(identity);
        }
      });
    }
  });
  const container = document.querySelector(teamsMeetingContainerSelectors[0]) || document.body;
  bodyObserver.observe(container, { childList: true, subtree: true });

  // Periodic rescan: tiles re-render without childList mutations sometimes.
  const rescanInterval = setInterval(() => { if (!destroyed) scanAndObserveAll(); }, 5000) as unknown as number;

  // Heartbeat: re-assert who is currently speaking every ~2s even without a state
  // change, so a consumer that started mid-turn (a reconnected WS / a restarted
  // pipeline with an empty name-binder) learns the active speaker WITHOUT waiting
  // for the next blue-square transition. Mirrors the Zoom active-speaker heartbeat;
  // repeated same-speaker hints are idempotent at the binder.
  const heartbeat = setInterval(() => {
    if (destroyed) return;
    for (const [id, st] of speakingStates) {
      if (st !== 'speaking') continue;
      for (const ident of cache.values()) {
        if (ident.id !== id) continue;
        if (!ident.name) ident.name = extractName(ident.element);   // name may have rendered since
        if (ident.name) opts.onSpeaking(ident.name, ident.id, false, Date.now());
        break;
      }
    }
  }, 2000) as unknown as number;

  return {
    getSpeaking(): string[] {
      const names: string[] = [];
      for (const [id, st] of speakingStates) {
        if (st !== 'speaking') continue;
        for (const ident of cache.values()) if (ident.id === id) { names.push(ident.name); break; }
      }
      return names;
    },
    destroy(): void {
      destroyed = true;
      clearInterval(rescanInterval);
      clearInterval(heartbeat);
      bodyObserver.disconnect();
      for (const obs of observers.values()) obs.forEach(o => o.disconnect());
      observers.clear();
      for (const raf of rafHandles.values()) cancelAnimationFrame(raf);
      rafHandles.clear();
      for (const t of debounceTimers.values()) clearTimeout(t);
      debounceTimers.clear();
      states.clear();
      speakingStates.clear();
      cache.clear();
    },
  };
}
