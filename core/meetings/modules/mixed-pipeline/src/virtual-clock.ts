/**
 * A clock driven by a tape instead of by the wall.
 *
 * The mixed lane has exactly two wall-clock dependencies — a 1 s heartbeat that ticks, rolls and
 * TTL-finalizes the open turn, and the TTL's own comparison — and they are the reason a replay had
 * to run at 1x to be worth anything. Fired instantly, a turn that lived through several growing
 * submissions is transcribed in ONE, and every defect that lives in the CADENCE (a name locked on
 * the first tick, a draft retracted before it confirmed) simply does not occur.
 *
 * Paying real time for that is a waste: the tape already carries the timestamps. This advances the
 * lane's clock to each event's own time and fires the heartbeats that fall in between, in order, so
 * the same branches run in the same sequence as they did live — in seconds rather than minutes.
 *
 * The one subtlety, and it is the whole correctness argument: the lane is ASYNCHRONOUS. A heartbeat
 * enqueues work that a promise chain drains later, so advancing the clock without letting that
 * chain settle would run the next heartbeat against a state the live lane never had. Every step
 * therefore awaits a drain, which is why this exposes an async advance rather than a bare setter.
 */
export interface VirtualClockOptions {
  /** Where the clock starts (the tape's first timestamp). */
  startMs: number;
  /** Let the lane's promise chain settle. Called after every event and every heartbeat. */
  drain: () => Promise<void>;
}

interface Heartbeat { fn: () => void; everyMs: number; nextAt: number; cancelled: boolean }

export class VirtualClock {
  private t: number;
  private beats: Heartbeat[] = [];
  private fired = 0;

  constructor(private readonly opts: VirtualClockOptions) {
    this.t = opts.startMs;
  }

  now(): number { return this.t; }
  heartbeatsFired(): number { return this.fired; }

  /**
   * How far the lane's clock has been moved since the tape's first timestamp — that is, the real
   * waiting this tier replaced. Paired with `heartbeatsFired()` it states the tier's whole claim as
   * two numbers the run can report about ITSELF: this much lane time passed, this many heartbeats
   * fired inside it. `virtual-time-equivalence.test.ts` asserts against these rather than against
   * how long a 1x run happened to take on the same machine, so the check measures the mechanism and
   * not the runner.
   */
  advancedMs(): number { return this.t - this.opts.startMs; }

  setHeartbeat(fn: () => void, everyMs: number): () => void {
    const beat: Heartbeat = { fn, everyMs, nextAt: this.t + everyMs, cancelled: false };
    this.beats.push(beat);
    return () => { beat.cancelled = true; };
  }

  /**
   * Move to `to`, firing every heartbeat due on the way, each at ITS OWN time — not batched at the
   * destination. A heartbeat that believes it is running 4 s later than it is makes different
   * decisions about a TTL, which is exactly the class of defect this tier exists to reproduce.
   */
  async advanceTo(to: number): Promise<void> {
    if (to < this.t) return;                      // tapes are replayed in order; never run backwards
    for (;;) {
      let due: Heartbeat | null = null;
      for (const b of this.beats) {
        if (b.cancelled || b.nextAt > to) continue;
        if (!due || b.nextAt < due.nextAt) due = b;
      }
      if (!due) break;
      this.t = due.nextAt;
      due.nextAt += due.everyMs;
      this.fired++;
      try { due.fn(); } catch { /* a heartbeat fault is the lane's business, not the clock's */ }
      await this.opts.drain();
    }
    this.t = to;
  }
}
