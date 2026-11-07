/** stick-to-bottom — follow the stream only while the reader is at the bottom (Vexa-ai/vexa#1599).
 *
 *  > "chat should not fight with me when it's outputting text and i scroll up to scroll chat
 *  >  history, it should get down if it do not scroll only"  — founder, 2026-09-06
 *
 *  The three cases the founder's sentence names are pinned here, plus the one that made the old
 *  implementation fail: a scroll-up whose `scroll` event has not been dispatched yet. That is the
 *  ONLY difference between a follower that works and one that fights, and it is invisible to a test
 *  that always delivers the event first — so the fake scroller below moves without notifying, the
 *  way a real trackpad gesture does between two frames.
 */
import { describe, it, expect, vi } from "vitest";
import { createStickToBottom, type ScrollBox } from "../stickToBottom";

/** A scroll container with no DOM: content grows downward, scrollTop clamps like a real one. */
class FakeScroller implements ScrollBox {
  clientHeight = 400;
  scrollHeight = 1000;
  private pos = 0;

  get scrollTop() { return this.pos; }
  set scrollTop(v: number) { this.pos = Math.max(0, Math.min(v, this.maxTop)); }
  get maxTop() { return this.scrollHeight - this.clientHeight; }

  /** A streamed chunk renders: the transcript gets taller, the viewport does not move. */
  grow(px = 200) { this.scrollHeight += px; }
  /** A human drags the scrollbar / spins the wheel. Negative is UP, toward the history. */
  humanScroll(px: number) { this.scrollTop = this.pos + px; }
}

/** A box already parked at the bottom, following, the way an open chat is. */
function atBottom() {
  const box = new FakeScroller();
  const seen: boolean[] = [];
  const stick = createStickToBottom(() => box, { onFollowingChange: (f) => seen.push(f) });
  stick.onContent();                       // first paint pins to the bottom
  expect(box.scrollTop).toBe(box.maxTop);
  return { box, stick, seen };
}

describe("stickToBottom", () => {
  it("follows the stream while the reader is at the bottom", () => {
    const { box, stick } = atBottom();
    for (let i = 0; i < 5; i++) { box.grow(120); stick.onContent(); }
    expect(stick.following()).toBe(true);
    expect(box.scrollTop).toBe(box.maxTop);
  });

  it("never moves a transcript the reader has scrolled up, however much text arrives", () => {
    const { box, stick } = atBottom();
    box.humanScroll(-300);
    stick.onScroll();                      // the event lands, as it usually does
    const parked = box.scrollTop;
    expect(stick.following()).toBe(false);

    for (let i = 0; i < 20; i++) { box.grow(150); stick.onContent(); }
    expect(box.scrollTop).toBe(parked);    // exactly where the reader put it
    expect(stick.following()).toBe(false);
  });

  it("detaches on a scroll-up whose scroll event has not been dispatched yet", () => {
    // THE REGRESSION. `scroll` fires at the frame boundary, a chunk arrives on a network task in
    // between; a follower that trusts the last event yanks the view back down mid-gesture.
    const { box, stick } = atBottom();
    box.humanScroll(-250);                 // no onScroll() — the event is still queued
    const parked = box.scrollTop;

    stick.onContent();                     // chunk arrives first
    expect(box.scrollTop).toBe(parked);
    expect(stick.following()).toBe(false);

    stick.onScroll();                      // the event finally arrives; nothing changes
    expect(box.scrollTop).toBe(parked);
    expect(stick.following()).toBe(false);
  });

  it("re-engages when the reader returns to the bottom", () => {
    const { box, stick } = atBottom();
    box.humanScroll(-400);
    stick.onScroll();
    expect(stick.following()).toBe(false);

    box.humanScroll(400);                  // back down to the bottom
    stick.onScroll();
    expect(stick.following()).toBe(true);

    box.grow(180);
    stick.onContent();
    expect(box.scrollTop).toBe(box.maxTop);
  });

  it("re-engages from the position alone when the return-to-bottom event is late", () => {
    const { box, stick } = atBottom();
    box.humanScroll(-400);
    stick.onScroll();
    box.humanScroll(400);                  // at the bottom again, event still queued
    box.grow(200);
    stick.onContent();
    expect(stick.following()).toBe(true);
    expect(box.scrollTop).toBe(box.maxTop);
  });

  it("stays detached a few px from the bottom, and follows within the epsilon", () => {
    const { box, stick } = atBottom();
    box.humanScroll(-4);                   // sub-pixel slack, not a decision to read history
    stick.onScroll();
    expect(stick.following()).toBe(true);

    box.humanScroll(-40);
    stick.onScroll();
    expect(stick.following()).toBe(false);
  });

  it("scrolls to the bottom when the person sends a message, from wherever they were", () => {
    const { box, stick } = atBottom();
    box.humanScroll(-600);
    stick.onScroll();
    expect(stick.following()).toBe(false);

    stick.pin();                           // onSubmit — the human asked for this reply
    expect(stick.following()).toBe(true);
    expect(box.scrollTop).toBe(box.maxTop);

    box.grow(300);                         // …and the reply that follows keeps the view pinned
    stick.onContent();
    expect(box.scrollTop).toBe(box.maxTop);
  });

  it("reports following changes once per transition, for the jump-to-latest affordance", () => {
    const { box, stick, seen } = atBottom();
    expect(seen).toEqual([]);              // opened following; nothing to report

    box.humanScroll(-300); stick.onScroll();
    box.grow(100); stick.onContent();      // still detached — must not re-report
    box.grow(100); stick.onContent();
    expect(seen).toEqual([false]);

    stick.pin();
    expect(seen).toEqual([false, true]);
  });

  it("does nothing, and never throws, before the container is mounted", () => {
    const changed = vi.fn();
    const stick = createStickToBottom(() => null, { onFollowingChange: changed });
    expect(() => { stick.onScroll(); stick.onContent(); stick.pin(); }).not.toThrow();
    expect(changed).not.toHaveBeenCalled();
    expect(stick.following()).toBe(true);
  });
});
