import { Page } from 'playwright-core';
import { log } from '../utils';

/**
 * Platform-specific CSS selectors used to locate participant names
 * associated with media elements in the DOM.
 *
 * These are intentionally coarse for the first iteration — they will be
 * refined as we test against real meeting UIs.
 */
const PLATFORM_SELECTORS: Record<string, {
  /** Selectors for the container that wraps a participant tile / video */
  participantContainer: string[];
  /** Selectors for the element inside the container that holds the name */
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
      '[data-self-name]',       // self-name attribute holds the name directly
      'span.notranslate',       // primary name span in Google Meet
      '.zWGUib',                // Google Meet name class
      '.cS7aqe.N2K3jd',        // alternative name class
      '.XWGOtd',               // another name class
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

/** Simple in-memory cache: mediaElement index -> resolved name */
const speakerNameCache = new Map<string, string>();

/**
 * Build a cache key from platform + element index so we don't
 * re-query the DOM for the same participant.
 */
function cacheKey(platform: string, elementIndex: number): string {
  return `${platform}:${elementIndex}`;
}

/**
 * Google Meet-specific name resolution.
 *
 * In Google Meet, <audio> elements are NOT inside participant tiles — they
 * live in a separate part of the DOM. Walking up from an audio element will
 * never find a participant container. Instead we use data already collected
 * by the browser-side speaker detection (recording.ts), which exposes:
 *
 *   window.__vexaGetAllParticipantNames()
 *     → { names: Record<participantId, displayName>, speaking: string[] }
 *
 *   window.__vexaSpeakerEvents
 *     → Array<{ event_type, participant_name, participant_id, relative_timestamp_ms }>
 *
 * Resolution strategy (ordered by reliability):
 *  1. If exactly one participant is currently speaking, use that name.
 *     (When audio arrives for a track, someone must be speaking.)
 *  2. Use the most recent SPEAKER_START event from __vexaSpeakerEvents
 *     that has not been followed by a SPEAKER_END for the same participant.
 *  3. Map by element index → participant tile index (positional).
 *  4. Fall back to "Speaker N".
 */
async function resolveGoogleMeetSpeakerName(
  page: Page,
  elementIndex: number,
): Promise<string | null> {
  try {
    const result = await page.evaluate((idx: number) => {
      // Strategy 1: Use the live participant name lookup exposed by recording.ts
      // __vexaGetAllParticipantNames() scans DOM participant tiles and returns
      // all names keyed by participant-id plus the currently-speaking subset.
      const getNames = (window as any).__vexaGetAllParticipantNames;
      if (typeof getNames === 'function') {
        const { names, speaking } = getNames() as {
          names: Record<string, string>;
          speaking: string[];
        };

        // Positional mapping: participant tile N → audio element N.
        // Google Meet creates one audio element per remote participant in the
        // same DOM order as participant tiles, making this the most reliable
        // strategy (it survives multiple people speaking simultaneously).
        const nameList = Object.values(names);
        if (idx < nameList.length) {
          return nameList[idx];
        }

        // If we have more audio elements than tiles (rare), fall back to
        // whoever is currently speaking.
        if (speaking.length === 1) {
          return speaking[0];
        }
        if (speaking.length > 1) {
          return speaking[0]; // first match — best we can do
        }
      }

      // Strategy 2: Fall back to speaker events history (__vexaSpeakerEvents)
      // accumulated by the speaker detection polling in recording.ts.
      const events: Array<{
        event_type: string;
        participant_name: string;
        participant_id: string;
        relative_timestamp_ms: number;
      }> = (window as any).__vexaSpeakerEvents || [];

      if (events.length > 0) {
        // Collect unique participant names in order of first appearance
        const seen = new Set<string>();
        const orderedNames: string[] = [];
        for (const evt of events) {
          if (!seen.has(evt.participant_id)) {
            seen.add(evt.participant_id);
            orderedNames.push(evt.participant_name);
          }
        }
        if (idx < orderedNames.length) {
          return orderedNames[idx];
        }

        // More audio elements than known participants — return most recent speaker
        const lastStart = [...events].reverse().find(e => e.event_type === 'SPEAKER_START');
        if (lastStart) {
          return lastStart.participant_name;
        }
      }

      return null;
    }, elementIndex);

    return result;
  } catch (err: any) {
    log(`[SpeakerIdentity] Google Meet browser query failed: ${err.message}`);
    return null;
  }
}

/**
 * Resolve the participant display name for a given media element by
 * inspecting the surrounding DOM.
 *
 * The lookup is performed once per element and cached — subsequent calls
 * for the same element return the cached value immediately.
 *
 * @param page      - Playwright Page handle for the meeting tab
 * @param elementIndex - the index of the media element (from findMediaElements)
 * @param platform  - 'googlemeet' | 'msteams'
 * @returns the participant name, or a fallback like "Speaker 1"
 */
export async function resolveSpeakerName(
  page: Page,
  elementIndex: number,
  platform: string
): Promise<string> {
  const key = cacheKey(platform, elementIndex);
  const cached = speakerNameCache.get(key);
  if (cached) {
    return cached;
  }

  // Google Meet: audio elements are NOT inside participant tiles.
  // Use browser-side speaker detection data instead of DOM traversal.
  if (platform === 'googlemeet') {
    const gmName = await resolveGoogleMeetSpeakerName(page, elementIndex);
    if (gmName) {
      speakerNameCache.set(key, gmName);
      log(`[SpeakerIdentity] Element ${elementIndex} → "${gmName}" (platform: ${platform}, via speaker detection)`);
      return gmName;
    }
    // Fall through to generic DOM strategy as last resort
  }

  const selectors = PLATFORM_SELECTORS[platform];
  if (!selectors) {
    const fallback = `Speaker ${elementIndex + 1}`;
    log(`[SpeakerIdentity] Unknown platform "${platform}", using fallback: ${fallback}`);
    speakerNameCache.set(key, fallback);
    return fallback;
  }

  const name = await page.evaluate(
    ({ idx, containerSelectors, nameSelectors }) => {
      // Gather all active media elements in DOM order (same logic as findMediaElements)
      const mediaElements = Array.from(
        document.querySelectorAll('audio, video')
      ).filter((el: any) =>
        !el.paused &&
        el.srcObject instanceof MediaStream &&
        (el.srcObject as MediaStream).getAudioTracks().length > 0
      );

      const targetElement = mediaElements[idx] as HTMLElement | undefined;
      if (!targetElement) return null;

      // Strategy 1: Walk up from the media element to find a participant container,
      // then look inside for a name element.
      let current: HTMLElement | null = targetElement;
      while (current && current !== document.body) {
        for (const cs of containerSelectors) {
          if (current.matches(cs)) {
            // Found a participant container — look for a name inside
            for (const ns of nameSelectors) {
              // Check for data-self-name attribute first
              if (ns === '[data-self-name]') {
                const selfName = current.getAttribute('data-self-name');
                if (selfName) return selfName;
              }
              const nameEl = current.querySelector(ns);
              if (nameEl) {
                const text = (nameEl.textContent || '').trim();
                if (text.length > 0) return text;
                // Try title attribute (Teams uses span[title])
                const title = nameEl.getAttribute('title');
                if (title && title.trim().length > 0) return title.trim();
              }
            }
          }
        }
        current = current.parentElement;
      }

      // Strategy 2: Check aria-label on the media element itself or its parent
      const ariaLabel = targetElement.getAttribute('aria-label');
      if (ariaLabel && ariaLabel.trim().length > 0) return ariaLabel.trim();

      // Strategy 3: Check the closest element with a title attribute
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

  const resolvedName = name || `Speaker ${elementIndex + 1}`;
  speakerNameCache.set(key, resolvedName);
  log(`[SpeakerIdentity] Element ${elementIndex} → "${resolvedName}" (platform: ${platform})`);
  return resolvedName;
}

/**
 * Clear the speaker name cache. Useful when participants change
 * (e.g., someone leaves and a new person takes their media element slot).
 */
export function clearSpeakerNameCache(): void {
  speakerNameCache.clear();
  log('[SpeakerIdentity] Cache cleared.');
}

/**
 * Remove a single entry from the cache (e.g., when a participant leaves).
 */
export function invalidateSpeakerName(platform: string, elementIndex: number): void {
  speakerNameCache.delete(cacheKey(platform, elementIndex));
}
