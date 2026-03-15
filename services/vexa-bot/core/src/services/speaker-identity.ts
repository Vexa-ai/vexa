import { Page } from 'playwright-core';
import { log } from '../utils';

/**
 * Platform-specific CSS selectors used to locate participant names
 * associated with media elements in the DOM.
 */
const PLATFORM_SELECTORS: Record<string, {
  participantContainer: string[];
  nameElement: string[];
}> = {
  googlemeet: {
    participantContainer: [
      '[data-participant-id]',
      '[data-self-name]',
      '.participant-tile',
      '.video-tile',
    ],
    nameElement: [
      '[data-self-name]',
      'span.notranslate',
      '.zWGUib',
      '.cS7aqe.N2K3jd',
      '.XWGOtd',
      '.participant-name',
      '.display-name',
    ],
  },
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

// ─── Track-to-Speaker Correlation Map ────────────────────────────────────────
//
// Instead of guessing on each audio chunk, we build a mapping over time:
// - When audio arrives on track N and exactly one speaking indicator is active,
//   that's a "vote" for track N = that speaker.
// - After enough consistent votes, we lock the mapping with high confidence.
// - This survives simultaneous speech because the mapping was built during
//   single-speaker moments.

interface TrackVote {
  name: string;
  count: number;
}

interface TrackMapping {
  /** Confirmed speaker name for this track */
  name: string;
  /** How many consistent votes confirmed this mapping */
  confidence: number;
  /** Whether the mapping is locked (enough votes) */
  locked: boolean;
  /** Timestamp of last update */
  updatedAt: number;
}

/** Votes per track: trackIndex → { speakerName → voteCount } */
const trackVotes = new Map<number, Map<string, number>>();

/** Confirmed mappings: trackIndex → TrackMapping */
const trackMappings = new Map<number, TrackMapping>();

/** Minimum votes needed to lock a mapping */
const LOCK_THRESHOLD = 3;

/** After this many ms, allow re-evaluation of a locked mapping */
const LOCK_EXPIRY_MS = 60_000;

/**
 * Record a correlation vote: track N was active while speaker X was indicated.
 * Called from handlePerSpeakerAudioData when we have a speaking signal.
 */
export function recordTrackVote(trackIndex: number, speakerName: string): void {
  if (!trackVotes.has(trackIndex)) {
    trackVotes.set(trackIndex, new Map());
  }
  const votes = trackVotes.get(trackIndex)!;
  const current = votes.get(speakerName) || 0;
  votes.set(speakerName, current + 1);

  // Check if we can lock this mapping
  const totalVotes = Array.from(votes.values()).reduce((a, b) => a + b, 0);
  const topVote = Array.from(votes.entries()).sort((a, b) => b[1] - a[1])[0];

  if (topVote && topVote[1] >= LOCK_THRESHOLD) {
    const ratio = topVote[1] / totalVotes;
    if (ratio >= 0.7) { // 70%+ votes for same speaker → lock
      const existing = trackMappings.get(trackIndex);
      if (!existing || existing.name !== topVote[0]) {
        log(`[SpeakerIdentity] Track ${trackIndex} → "${topVote[0]}" LOCKED (${topVote[1]}/${totalVotes} votes, ${(ratio * 100).toFixed(0)}%)`);
      }
      trackMappings.set(trackIndex, {
        name: topVote[0],
        confidence: topVote[1],
        locked: true,
        updatedAt: Date.now(),
      });
    }
  }
}

/**
 * Get the confirmed speaker name for a track, if the mapping is locked.
 */
export function getLockedMapping(trackIndex: number): string | null {
  const mapping = trackMappings.get(trackIndex);
  if (!mapping || !mapping.locked) return null;

  // Check expiry
  if (Date.now() - mapping.updatedAt > LOCK_EXPIRY_MS) {
    mapping.locked = false;
    return null;
  }

  return mapping.name;
}

/**
 * Query the browser for current speaking state and participant names.
 * Returns the raw data for the caller to use for voting or resolution.
 */
async function queryBrowserState(
  page: Page,
  elementIndex: number,
  botName?: string,
): Promise<{ filteredNames: string[]; speaking: string[] } | null> {
  try {
    return await page.evaluate(({ idx, selfName }: { idx: number; selfName: string }) => {
      const isJunkName = (name: string): boolean => {
        return /^Google Participant \(/.test(name) ||
               /spaces\//.test(name) ||
               /devices\//.test(name);
      };

      const getNames = (window as any).__vexaGetAllParticipantNames;
      if (typeof getNames !== 'function') return null;

      const data = getNames() as {
        names: Record<string, string>;
        speaking: string[];
      };

      const selfLower = selfName.toLowerCase();
      const junkPatterns = ['let participants', 'send messages', 'turn on captions'];
      const filteredNames = Object.values(data.names).filter(n => {
        const lower = n.toLowerCase();
        if (lower.includes(selfLower) || selfLower.includes(lower)) return false;
        if (junkPatterns.some(p => lower.includes(p))) return false;
        if (isJunkName(n)) return false;
        return true;
      });
      const speaking = data.speaking.filter(n => !isJunkName(n));

      return { filteredNames, speaking };
    }, { idx: elementIndex, selfName: botName || 'Vexa Bot' });
  } catch (err: any) {
    log(`[SpeakerIdentity] Browser query failed: ${err.message}`);
    return null;
  }
}

/**
 * Resolve speaker name for a Google Meet audio track.
 *
 * Strategy:
 * 1. Check locked track mapping (built from correlation votes)
 * 2. If exactly one person speaking → use that name + record vote
 * 3. Speaker events history
 * 4. Positional fallback (weakest)
 */
async function resolveGoogleMeetSpeakerName(
  page: Page,
  elementIndex: number,
  botName?: string,
): Promise<string | null> {
  // Strategy 1: Use locked mapping if available
  const locked = getLockedMapping(elementIndex);
  if (locked) {
    return locked;
  }

  // Query browser for current state
  const state = await queryBrowserState(page, elementIndex, botName);
  if (!state) return null;

  const { filteredNames, speaking } = state;

  // Strategy 2: Speaking signal → use name AND record vote for this track
  // But only if this name isn't already locked to a DIFFERENT track
  if (speaking.length === 1) {
    const speakingName = speaking[0];
    let alreadyLockedElsewhere = false;
    for (const [otherIdx, mapping] of trackMappings) {
      if (otherIdx !== elementIndex && mapping.locked && mapping.name === speakingName) {
        alreadyLockedElsewhere = true;
        break;
      }
    }
    if (!alreadyLockedElsewhere) {
      recordTrackVote(elementIndex, speakingName);
      return speakingName;
    }
    // Speaking name is locked to another track — this track belongs to someone else.
    // Don't assign, let re-resolution find the correct name later.
    log(`[SpeakerIdentity] Track ${elementIndex}: "${speakingName}" already locked to another track, skipping`);
  }

  // If multiple speaking, we can't vote but check if any match our existing votes
  if (speaking.length > 1) {
    const votes = trackVotes.get(elementIndex);
    if (votes) {
      // Return the speaking name that has the most votes for this track
      let bestName = '';
      let bestCount = 0;
      for (const name of speaking) {
        const count = votes.get(name) || 0;
        if (count > bestCount) {
          bestCount = count;
          bestName = name;
        }
      }
      if (bestName && bestCount > 0) {
        log(`[SpeakerIdentity] Track ${elementIndex} → "${bestName}" (multi-speaker, best vote match)`);
        return bestName;
      }
    }
  }

  // Strategy 3: Speaker events — most recent unended SPEAKER_START
  try {
    const eventsResult = await page.evaluate(() => {
      const events: Array<{
        event_type: string;
        participant_name: string;
        participant_id: string;
      }> = (window as any).__vexaSpeakerEvents || [];

      if (events.length === 0) return null;

      const isJunkName = (name: string): boolean => {
        return /^Google Participant \(/.test(name) ||
               /spaces\//.test(name) ||
               /devices\//.test(name);
      };

      const activeByParticipant = new Map<string, string>();
      for (const evt of events) {
        if (evt.event_type === 'SPEAKER_START') {
          activeByParticipant.set(evt.participant_id, evt.participant_name);
        } else if (evt.event_type === 'SPEAKER_END') {
          activeByParticipant.delete(evt.participant_id);
        }
      }

      const activeNames = Array.from(activeByParticipant.values()).filter(n => !isJunkName(n));
      if (activeNames.length === 1) return activeNames[0];

      const lastStart = [...events].reverse().find(e =>
        e.event_type === 'SPEAKER_START' && !isJunkName(e.participant_name)
      );
      return lastStart?.participant_name || null;
    });

    if (eventsResult) return eventsResult;
  } catch {}

  // No fallback — return null, caller gets empty string
  return null;
}

/**
 * Resolve the participant display name for a given media element.
 *
 * For Google Meet: uses correlation-based track mapping.
 * For other platforms: walks up the DOM from the media element.
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
    // No mapping yet — return empty string, not "Presentation".
    // The caller will re-resolve aggressively until a real name is found.
    log(`[SpeakerIdentity] Element ${elementIndex} → "" (platform: ${platform}, no mapping yet)`);
    return '';
  }

  const selectors = PLATFORM_SELECTORS[platform];
  if (!selectors) {
    log(`[SpeakerIdentity] Unknown platform "${platform}", no selectors — returning empty`);
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

  // No fallback — return what we found, or empty string
  log(`[SpeakerIdentity] Element ${elementIndex} → "${name || ''}" (platform: ${platform})`);
  return name || '';
}

/**
 * Clear all track mappings and votes. Call when meeting resets.
 */
export function clearSpeakerNameCache(): void {
  trackVotes.clear();
  trackMappings.clear();
  log('[SpeakerIdentity] All track mappings cleared.');
}

/**
 * Remove mapping for a single track (e.g., when a participant leaves).
 */
export function invalidateSpeakerName(platform: string, elementIndex: number): void {
  trackVotes.delete(elementIndex);
  trackMappings.delete(elementIndex);
  log(`[SpeakerIdentity] Track ${elementIndex} mapping invalidated.`);
}

/**
 * Get current mapping state for debugging.
 */
export function getTrackMappingState(): Record<number, { name: string; confidence: number; locked: boolean }> {
  const state: Record<number, { name: string; confidence: number; locked: boolean }> = {};
  for (const [idx, mapping] of trackMappings) {
    state[idx] = { name: mapping.name, confidence: mapping.confidence, locked: mapping.locked };
  }
  return state;
}
