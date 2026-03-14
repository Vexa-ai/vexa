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
