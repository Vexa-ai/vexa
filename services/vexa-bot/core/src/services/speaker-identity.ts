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

/** Minimum votes to lock (reduced from 3 for faster locking with human participants) */
const LOCK_THRESHOLD = 2;

/** Minimum vote ratio to lock (70%) */
const LOCK_RATIO = 0.7;

/** Track last audio activity time per track (for Zoom active-speaker disambiguation) */
const trackLastAudioMs = new Map<number, number>();

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
 * Record a vote: track N was active while speaker X was speaking.
 * Supports fractional weights (0.5 for overlapping speech, 1.0 for exclusive).
 * Once locked, votes are ignored for that track.
 */
export function recordTrackVote(trackIndex: number, speakerName: string, weight: number = 1.0): void {
  // Already locked — nothing to do
  if (lockedMappings.has(trackIndex)) return;

  // Don't vote for a name already locked to another track
  if (isNameTaken(speakerName, trackIndex)) return;

  if (!trackVotes.has(trackIndex)) {
    trackVotes.set(trackIndex, new Map());
  }
  const votes = trackVotes.get(trackIndex)!;
  votes.set(speakerName, (votes.get(speakerName) || 0) + weight);

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

/**
 * Report that a track just received audio data.
 * Called from the audio pipeline so Zoom active-speaker voting
 * can disambiguate which track the highlighted name belongs to.
 */
export function reportTrackAudio(trackIndex: number): void {
  trackLastAudioMs.set(trackIndex, Date.now());
}

/**
 * Is this track the most recently active one? (within 500ms tolerance)
 * Used by Zoom to vote active speaker name only on the loudest/most-recent track.
 */
function isMostRecentlyActiveTrack(trackIndex: number): boolean {
  const myTime = trackLastAudioMs.get(trackIndex) || 0;
  if (myTime === 0) return false;
  for (const [idx, time] of trackLastAudioMs) {
    if (idx !== trackIndex && time > myTime + 500) return false;
  }
  return true;
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

  // Single speaker → full vote (high confidence)
  if (speaking.length === 1) {
    const candidate = speaking[0];
    if (!isNameTaken(candidate, elementIndex)) {
      recordTrackVote(elementIndex, candidate, 1.0);
      return getLockedMapping(elementIndex) || candidate;
    }
  }

  // Two speakers overlapping → half vote for each (common in real conversation)
  if (speaking.length === 2) {
    for (const candidate of speaking) {
      if (!isNameTaken(candidate, elementIndex)) {
        recordTrackVote(elementIndex, candidate, 0.5);
      }
    }
    // Return locked name if just locked, or top voted
    const justLocked = getLockedMapping(elementIndex);
    if (justLocked) return justLocked;
  }

  // Zero or 3+ speaking — can't vote.
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

// ─── Teams DOM Traversal ─────────────────────────────────────────────────────

const TEAMS_SELECTORS = {
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
};

/**
 * DOM traversal for Teams: walk up from a media element to find a name.
 */
async function traverseTeamsDOM(page: Page, elementIndex: number): Promise<string | null> {
  return await page.evaluate(
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
      containerSelectors: TEAMS_SELECTORS.participantContainer,
      nameSelectors: TEAMS_SELECTORS.nameElement,
    }
  );
}

/**
 * Teams speaker resolution: DOM traversal + voting/locking (same system as Google Meet).
 * DOM traversal provides the name candidate, voting provides consistency and uniqueness.
 */
async function resolveTeamsSpeakerName(
  page: Page,
  elementIndex: number,
): Promise<string | null> {
  // Locked → permanent, instant return
  const locked = getLockedMapping(elementIndex);
  if (locked) return locked;

  // Try DOM traversal
  const domName = await traverseTeamsDOM(page, elementIndex);

  if (domName) {
    // Don't return a name already taken by another track
    if (isNameTaken(domName, elementIndex)) return null;

    recordTrackVote(elementIndex, domName);
    return getLockedMapping(elementIndex) || domName;
  }

  // No DOM name — return top voted if not taken
  const votes = trackVotes.get(elementIndex);
  if (votes && votes.size > 0) {
    const sorted = Array.from(votes.entries()).sort((a, b) => b[1] - a[1]);
    for (const [name] of sorted) {
      if (!isNameTaken(name, elementIndex)) return name;
    }
  }

  return null;
}

// ─── Zoom Speaker Resolution ─────────────────────────────────────────────────

/**
 * Zoom DOM traversal: walk up from audio element to find participant name.
 * Zoom web client wraps each participant's media in a video-avatar container
 * with a .video-avatar__avatar-footer label containing the name.
 */
async function traverseZoomDOM(page: Page, elementIndex: number): Promise<string | null> {
  return await page.evaluate(
    ({ idx }) => {
      const mediaElements = Array.from(
        document.querySelectorAll('audio, video')
      ).filter((el: any) =>
        !el.paused &&
        el.srcObject instanceof MediaStream &&
        (el.srcObject as MediaStream).getAudioTracks().length > 0
      );

      const targetElement = mediaElements[idx] as HTMLElement | undefined;
      if (!targetElement) return null;

      // Walk up the DOM tree looking for Zoom participant containers
      let current: HTMLElement | null = targetElement;
      while (current && current !== document.body) {
        // Check for video-avatar container
        if (current.classList.contains('video-avatar__avatar') ||
            current.querySelector('.video-avatar__avatar-footer')) {
          const footer = current.querySelector('.video-avatar__avatar-footer');
          if (footer) {
            const span = footer.querySelector('span');
            const text = (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || null;
            if (text && text.length > 0) return text;
          }
        }
        // Check for speaker-active-container (main speaker view)
        if (current.classList.contains('speaker-active-container__video-frame')) {
          const footer = current.querySelector('.video-avatar__avatar-footer');
          if (footer) {
            const span = footer.querySelector('span');
            const text = (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || null;
            if (text && text.length > 0) return text;
          }
        }
        current = current.parentElement;
      }

      return null;
    },
    { idx: elementIndex }
  );
}

/**
 * Query Zoom active speaker from DOM (the participant currently highlighted).
 */
async function queryZoomActiveSpeaker(page: Page, botName?: string): Promise<string | null> {
  try {
    return await page.evaluate((selfName: string) => {
      // Get name from active speaker container
      function nameFromContainer(container: Element | null): string | null {
        if (!container) return null;
        const footer = container.querySelector('.video-avatar__avatar-footer');
        if (!footer) return null;
        const span = footer.querySelector('span');
        return (span?.textContent?.trim() || (footer as HTMLElement).innerText?.trim()) || null;
      }

      // Layout 1: Normal view — active speaker has a dedicated full-size container
      const name1 = nameFromContainer(document.querySelector('.speaker-active-container__video-frame'));
      if (name1) {
        const selfLower = selfName.toLowerCase();
        const nameLower = name1.toLowerCase();
        if (!nameLower.includes(selfLower) && !selfLower.includes(nameLower)) {
          return name1;
        }
      }

      // Layout 2: Screen-share view — active speaker tile has the --active modifier
      const name2 = nameFromContainer(document.querySelector('.speaker-bar-container__video-frame--active'));
      if (name2) {
        const selfLower = selfName.toLowerCase();
        const nameLower = name2.toLowerCase();
        if (!nameLower.includes(selfLower) && !selfLower.includes(nameLower)) {
          return name2;
        }
      }

      return null;
    }, botName || 'Vexa');
  } catch {
    return null;
  }
}

/**
 * Zoom speaker resolution: DOM traversal + active speaker correlation → voting → lock.
 * Same voting/locking system as GMeet and Teams.
 *
 * Path 1: DOM traversal from audio element → find participant name in ancestor tile.
 * Path 2: Active speaker query → if exactly one speaker highlighted, correlate with audio activity.
 */
async function resolveZoomSpeakerName(
  page: Page,
  elementIndex: number,
  botName?: string,
): Promise<string | null> {
  // Locked → permanent, instant return
  const locked = getLockedMapping(elementIndex);
  if (locked) return locked;

  // Path 1: DOM traversal from the audio element
  const domName = await traverseZoomDOM(page, elementIndex);
  if (domName) {
    if (!isNameTaken(domName, elementIndex)) {
      recordTrackVote(elementIndex, domName);
      return getLockedMapping(elementIndex) || domName;
    }
  }

  // Path 2: Active speaker correlation (like GMeet voting)
  // Only vote on the track that most recently had audio — prevents all tracks
  // from voting for the same highlighted name simultaneously.
  const activeSpeaker = await queryZoomActiveSpeaker(page, botName);
  if (activeSpeaker && isMostRecentlyActiveTrack(elementIndex)) {
    if (!isNameTaken(activeSpeaker, elementIndex)) {
      recordTrackVote(elementIndex, activeSpeaker, 1.0);
      return getLockedMapping(elementIndex) || activeSpeaker;
    }
  }

  // No name found — return top voted if not taken
  const votes = trackVotes.get(elementIndex);
  if (votes && votes.size > 0) {
    const sorted = Array.from(votes.entries()).sort((a, b) => b[1] - a[1]);
    for (const [name] of sorted) {
      if (!isNameTaken(name, elementIndex)) return name;
    }
  }

  return null;
}

// ─── Main Resolution ─────────────────────────────────────────────────────────

/**
 * Resolve speaker name for any platform.
 * Google Meet: speaking-indicator correlation → voting → permanent lock.
 * Teams: DOM traversal → voting → permanent lock.
 * Zoom: DOM traversal + active speaker correlation → voting → permanent lock.
 * All enforce one-name-per-track, one-track-per-name.
 */
export async function resolveSpeakerName(
  page: Page,
  elementIndex: number,
  platform: string,
  botName?: string,
): Promise<string> {
  let name: string | null = null;

  if (platform === 'googlemeet') {
    name = await resolveGoogleMeetSpeakerName(page, elementIndex, botName);
  } else if (platform === 'msteams') {
    name = await resolveTeamsSpeakerName(page, elementIndex);
  } else if (platform === 'zoom') {
    name = await resolveZoomSpeakerName(page, elementIndex, botName);
  } else {
    log(`[SpeakerIdentity] Unknown platform "${platform}" — returning empty`);
    return '';
  }

  if (name) {
    log(`[SpeakerIdentity] Element ${elementIndex} → "${name}" (platform: ${platform})`);
    return name;
  }
  log(`[SpeakerIdentity] Element ${elementIndex} → "" (platform: ${platform}, not yet mapped)`);
  return '';
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

/** Clear all mappings. Call only when meeting resets. */
export function clearSpeakerNameCache(): void {
  trackVotes.clear();
  lockedMappings.clear();
  trackLastAudioMs.clear();
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
