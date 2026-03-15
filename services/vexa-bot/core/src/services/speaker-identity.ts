import { Page } from 'playwright-core';
import { log } from '../utils';

/**
 * Speaker Identity — discover track→speaker mapping once, lock forever.
 *
 * Google Meet assigns each participant a fixed audio track for the duration
 * of the meeting. The mapping never changes (unless someone leaves and
 * rejoins). We discover it by correlating audio activity with speaking
 * indicators, then lock it permanently.
 *
 * Strategy:
 * 1. When audio arrives on track N and exactly one speaking indicator is active,
 *    record a vote: track N = that speaker.
 * 2. After LOCK_THRESHOLD consistent votes → lock permanently.
 * 3. Locked mappings are never re-evaluated (the mapping is static).
 * 4. If a name is already taken by another track (locked OR leading votes),
 *    don't return it — enforce one-name-per-track, one-track-per-name always.
 */

// ─── Track→Speaker Mapping ───────────────────────────────────────────────────

/** Votes per track: trackIndex → { speakerName → voteCount } */
const trackVotes = new Map<number, Map<string, number>>();

/** Locked mappings: trackIndex → speakerName. Once set, permanent. */
const lockedMappings = new Map<number, string>();

/** Minimum votes to lock */
const LOCK_THRESHOLD = 3;

/** Minimum vote ratio to lock (70%) */
const LOCK_RATIO = 0.7;

/**
 * Check if a name is already taken by another track.
 * "Taken" means locked to another track.
 */
export function isNameTaken(name: string, excludeTrackIndex?: number): boolean {
  for (const [idx, lockedName] of lockedMappings) {
    if (idx !== excludeTrackIndex && lockedName === name) return true;
  }
  return false;
}

/**
 * Record a vote: track N was active while speaker X was the only one speaking.
 * Once locked, votes are ignored for that track.
 */
export function recordTrackVote(trackIndex: number, speakerName: string): void {
  // Already locked — nothing to do
  if (lockedMappings.has(trackIndex)) return;

  // Don't vote for a name already locked to another track
  if (isNameTaken(speakerName, trackIndex)) return;

  if (!trackVotes.has(trackIndex)) {
    trackVotes.set(trackIndex, new Map());
  }
  const votes = trackVotes.get(trackIndex)!;
  votes.set(speakerName, (votes.get(speakerName) || 0) + 1);

  // Check if we can lock
  const totalVotes = Array.from(votes.values()).reduce((a, b) => a + b, 0);
  const topEntry = Array.from(votes.entries()).sort((a, b) => b[1] - a[1])[0];

  if (topEntry && topEntry[1] >= LOCK_THRESHOLD && topEntry[1] / totalVotes >= LOCK_RATIO) {
    // Final check: don't lock if the name is taken
    if (isNameTaken(topEntry[0], trackIndex)) {
      log(`[SpeakerIdentity] Track ${trackIndex} would lock to "${topEntry[0]}" but name is taken by another track — skipping`);
      return;
    }
    lockedMappings.set(trackIndex, topEntry[0]);
    log(`[SpeakerIdentity] Track ${trackIndex} → "${topEntry[0]}" LOCKED PERMANENTLY (${topEntry[1]}/${totalVotes} votes, ${(topEntry[1] / totalVotes * 100).toFixed(0)}%)`);
  }
}

/**
 * Get locked speaker name for a track. Returns null if not yet locked.
 */
export function getLockedMapping(trackIndex: number): string | null {
  return lockedMappings.get(trackIndex) ?? null;
}

/**
 * Check if a track is locked.
 */
export function isTrackLocked(trackIndex: number): boolean {
  return lockedMappings.has(trackIndex);
}

// ─── Browser State Query ─────────────────────────────────────────────────────

/** Helper: reject junk names */
function isJunkName(name: string): boolean {
  return /^Google Participant \(/.test(name) ||
         /spaces\//.test(name) ||
         /devices\//.test(name);
}

/**
 * Query browser for participant names and who's currently speaking.
 */
async function queryBrowserState(
  page: Page,
  botName?: string,
): Promise<{ filteredNames: string[]; speaking: string[] } | null> {
  try {
    return await page.evaluate((selfName: string) => {
      const isJunk = (name: string): boolean => {
        return /^Google Participant \(/.test(name) ||
               /spaces\//.test(name) ||
               /devices\//.test(name);
      };

      const getNames = (window as any).__vexaGetAllParticipantNames;
      if (typeof getNames !== 'function') return null;

      const data = getNames() as { names: Record<string, string>; speaking: string[] };
      const selfLower = selfName.toLowerCase();
      const junkPatterns = ['let participants', 'send messages', 'turn on captions'];

      const filteredNames = Object.values(data.names).filter(n => {
        const lower = n.toLowerCase();
        if (lower.includes(selfLower) || selfLower.includes(lower)) return false;
        if (junkPatterns.some(p => lower.includes(p))) return false;
        if (isJunk(n)) return false;
        return true;
      });
      const speaking = data.speaking.filter(n => !isJunk(n));

      return { filteredNames, speaking };
    }, botName || 'Vexa Bot');
  } catch (err: any) {
    log(`[SpeakerIdentity] Browser query failed: ${err.message}`);
    return null;
  }
}

// ─── Main Resolution ─────────────────────────────────────────────────────────

/**
 * Resolve speaker name for a Google Meet audio track.
 *
 * If locked → return immediately (permanent).
 * If not locked → query browser, vote if single speaker.
 * Never return a name that's already taken by another track.
 */
async function resolveGoogleMeetSpeakerName(
  page: Page,
  elementIndex: number,
  botName?: string,
): Promise<string | null> {
  // Locked → permanent, instant return
  const locked = getLockedMapping(elementIndex);
  if (locked) return locked;

  // Query browser
  const state = await queryBrowserState(page, botName);
  if (!state) return null;

  const { speaking } = state;

  // Single speaker → vote
  if (speaking.length === 1) {
    const candidate = speaking[0];

    // Don't return a name already taken by another track
    if (isNameTaken(candidate, elementIndex)) {
      return null;
    }

    recordTrackVote(elementIndex, candidate);
    // Re-check lock (may have just locked)
    return getLockedMapping(elementIndex) || candidate;
  }

  // Multiple or zero speaking — can't vote.
  // Return top voted name only if it's not taken by another track.
  const votes = trackVotes.get(elementIndex);
  if (votes && votes.size > 0) {
    const sorted = Array.from(votes.entries()).sort((a, b) => b[1] - a[1]);
    for (const [name] of sorted) {
      if (!isNameTaken(name, elementIndex)) return name;
    }
  }

  return null;
}

/**
 * Resolve speaker name for any platform.
 * Google Meet: correlation-based voting → permanent lock.
 * Other platforms: DOM traversal from media element.
 */
export async function resolveSpeakerName(
  page: Page,
  elementIndex: number,
  platform: string,
  botName?: string,
): Promise<string> {
  if (platform === 'googlemeet') {
    const name = await resolveGoogleMeetSpeakerName(page, elementIndex, botName);
    if (name) {
      log(`[SpeakerIdentity] Element ${elementIndex} → "${name}" (platform: ${platform})`);
      return name;
    }
    log(`[SpeakerIdentity] Element ${elementIndex} → "" (platform: ${platform}, not yet mapped)`);
    return '';
  }

  // Other platforms: DOM traversal (Teams, etc.)
  const PLATFORM_SELECTORS: Record<string, {
    participantContainer: string[];
    nameElement: string[];
  }> = {
    msteams: {
      participantContainer: [
        '[data-tid*="video-tile"]',
        '[data-tid*="videoTile"]',
        '[data-tid*="participant"]',
        '[data-tid*="roster-item"]',
        '.participant-tile',
        '.video-tile',
      ],
      nameElement: [
        '[data-tid*="display-name"]',
        '[data-tid*="participant-name"]',
        '.participant-name',
        '.display-name',
        '.user-name',
        '.roster-item-name',
        '.video-tile-name',
        'span[title]',
        '.ms-Persona-primaryText',
      ],
    },
  };

  const selectors = PLATFORM_SELECTORS[platform];
  if (!selectors) {
    log(`[SpeakerIdentity] Unknown platform "${platform}" — returning empty`);
    return '';
  }

  const name = await page.evaluate(
    ({ idx, containerSelectors, nameSelectors }) => {
      const mediaElements = Array.from(
        document.querySelectorAll('audio, video')
      ).filter((el: any) =>
        !el.paused &&
        el.srcObject instanceof MediaStream &&
        (el.srcObject as MediaStream).getAudioTracks().length > 0
      );

      const targetElement = mediaElements[idx] as HTMLElement | undefined;
      if (!targetElement) return null;

      let current: HTMLElement | null = targetElement;
      while (current && current !== document.body) {
        for (const cs of containerSelectors) {
          if (current.matches(cs)) {
            for (const ns of nameSelectors) {
              if (ns === '[data-self-name]') {
                const selfName = current.getAttribute('data-self-name');
                if (selfName) return selfName;
              }
              const nameEl = current.querySelector(ns);
              if (nameEl) {
                const text = (nameEl.textContent || '').trim();
                if (text.length > 0) return text;
                const title = nameEl.getAttribute('title');
                if (title && title.trim().length > 0) return title.trim();
              }
            }
          }
        }
        current = current.parentElement;
      }

      const ariaLabel = targetElement.getAttribute('aria-label');
      if (ariaLabel && ariaLabel.trim().length > 0) return ariaLabel.trim();

      const titled = targetElement.closest('[title]');
      if (titled) {
        const title = titled.getAttribute('title');
        if (title && title.trim().length > 0) return title.trim();
      }

      return null;
    },
    {
      idx: elementIndex,
      containerSelectors: selectors.participantContainer,
      nameSelectors: selectors.nameElement,
    }
  );

  log(`[SpeakerIdentity] Element ${elementIndex} → "${name || ''}" (platform: ${platform})`);
  return name || '';
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

/** Clear all mappings. Call only when meeting resets. */
export function clearSpeakerNameCache(): void {
  trackVotes.clear();
  lockedMappings.clear();
  log('[SpeakerIdentity] All track mappings cleared.');
}

/** Remove mapping for a single track (participant left). */
export function invalidateSpeakerName(platform: string, elementIndex: number): void {
  trackVotes.delete(elementIndex);
  lockedMappings.delete(elementIndex);
  log(`[SpeakerIdentity] Track ${elementIndex} mapping invalidated.`);
}

/** Debug: get current mapping state. */
export function getTrackMappingState(): Record<number, { name: string; locked: boolean; votes: Record<string, number> }> {
  const state: Record<number, { name: string; locked: boolean; votes: Record<string, number> }> = {};
  for (const [idx, votes] of trackVotes) {
    const locked = lockedMappings.get(idx);
    state[idx] = {
      name: locked || '',
      locked: !!locked,
      votes: Object.fromEntries(votes),
    };
  }
  return state;
}
