/** stick-to-bottom — the chat transcript follows the stream ONLY while the reader is at the bottom.
 *
 *  > "chat should not fight with me when it's outputting text and i scroll up to scroll chat
 *  >  history, it should get down if it do not scroll only"
 *  >                                          — founder, 2026-09-06 (Vexa-ai/vexa#1599)
 *
 *  The check this replaces ("am I near the bottom?", recorded by the container's `scroll` handler)
 *  was not wrong, it was LATE. A `scroll` event is dispatched at the frame boundary; a streamed
 *  chunk arrives on a network task in between. So mid-gesture the follower reads a flag that still
 *  says at-the-bottom, scrolls, and the reader's scroll-up is undone before the event that would
 *  have cancelled it ever runs. At one chunk every few milliseconds that is not a glitch, it is the
 *  fight the founder described.
 *
 *  So the decision is taken from the POSITION, never from the event: appending content grows
 *  `scrollHeight` and never moves `scrollTop`, therefore any drop in `scrollTop` we did not cause
 *  ourselves is a human scrolling up. `onContent` compares the box against the position we last
 *  left it at and detaches on sight, whether or not the event has been delivered yet. The `scroll`
 *  handler still runs — it is the cheapest way to notice the reader coming back down — but nothing
 *  depends on it arriving in time.
 *
 *  Deliberately DOM-free: the caller wires `onScroll` to the container's scroll event and calls
 *  `onContent` after each render, so the whole rule is exercisable against a fake scroll box.
 */

/** The three numbers any scroller has. `HTMLDivElement` satisfies it; so does a test double. */
export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export interface StickToBottom {
  /** Is the view currently following new content? */
  following(): boolean;
  /** Wire to the container's `scroll` event: a human moved it, so the position is the answer. */
  onScroll(): void;
  /** New content landed (a streamed chunk, a job line, a loaded history). */
  onContent(): void;
  /** A human asked for the bottom — sending a message, or the jump-to-latest affordance. */
  pin(): void;
}

/** "At the bottom" tolerance in px. Sub-pixel layout and fractional device ratios leave a couple of
 *  px of slack at the true bottom; anything past that is a reader who has scrolled up on purpose. */
export const BOTTOM_EPSILON = 8;

const distanceFromBottom = (b: ScrollBox) => b.scrollHeight - b.scrollTop - b.clientHeight;

export function createStickToBottom(
  box: () => ScrollBox | null,
  opts: { epsilon?: number; onFollowingChange?: (following: boolean) => void } = {},
): StickToBottom {
  const epsilon = opts.epsilon ?? BOTTOM_EPSILON;
  let follow = true;
  let lastTop = 0;                 // where WE last left the box — anything lower is the reader's doing
  let lastHeight = 0;              // …and how tall it was then; anything taller is content, not them

  const setFollow = (next: boolean) => {
    if (next === follow) return;
    follow = next;
    opts.onFollowingChange?.(next);
  };
  const observe = (b: ScrollBox) => { lastTop = b.scrollTop; lastHeight = b.scrollHeight; };
  const toBottom = (b: ScrollBox) => {
    b.scrollTop = b.scrollHeight;  // the browser clamps to the max scroll; read back what it took
    observe(b);
  };

  return {
    following: () => follow,

    onScroll() {
      const b = box();
      if (!b) return;
      observe(b);
      setFollow(distanceFromBottom(b) <= epsilon);
    },

    onContent() {
      const b = box();
      if (!b) return;
      // The reader scrolled up and the event has not been dispatched yet: believe the position.
      if (b.scrollTop < lastTop - 1) { observe(b); setFollow(false); return; }
      if (!follow) {
        // Did they come back down? Measured against the height we LAST OBSERVED, because text that
        // arrived since is exactly what we are not following — judging against the new bottom would
        // read "returned to the bottom, then a chunk landed" as "still up in the history".
        const returned = lastHeight - b.scrollTop - b.clientHeight <= epsilon;
        observe(b);
        if (!returned) return;                        // still reading history — leave it alone
        setFollow(true);
      }
      toBottom(b);
    },

    pin() {
      const b = box();
      if (!b) return;
      setFollow(true);
      toBottom(b);
    },
  };
}
