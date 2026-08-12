/**
 * publish-stitch — rejoin, at the publish boundary, the sentences the lane had to cut.
 *
 * THE DEFECT. The mixed lane cuts for reasons that have nothing to do with where a sentence
 * ends: LocalAgreement confirms a leading whisper segment the moment it is stable, the
 * PROVISIONAL_CUT rolls an open turn every few seconds so a monologue's first final does not
 * wait for the speaker to pause, and the transport spine opens a fresh turn whenever it hands
 * over. Each of those is right on its own terms and each produces a ROW. On a 21-minute Teams
 * meeting that was 217 rows, 136 of them ending mid-thought and 42 of them two words or fewer —
 * a transcript the founder could not read, and the reason he could not assess the product.
 *
 * THE FIX IS NOT TO CUT LESS. Cutting late costs latency, and the cut is also what makes
 * word-level attribution possible. So the cut stays and the ASSEMBLY changes: consecutive
 * confirmed finals that belong to one speaker, one language and one continuous stretch of
 * speech are published as ONE ROW that GROWS, keyed on the first piece's segment id. The
 * collector upserts on that id, so the row lengthens in place.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO.
 *
 *  - It does not delay a final by one millisecond. The block's row is published the instant its
 *    first piece confirms; every later piece is an UPDATE to a row the customer already has.
 *    Buffering until a sentence completes would have been simpler and would have regressed
 *    exactly the latency the PROVISIONAL_CUT exists to protect.
 *  - It never merges two speakers. The merge key is the published name, and the one name that
 *    is not an identity — the provisional `Speaker`, which means "we could not say who" — is
 *    excluded, so two unattributable spans are never fused into one person. A stable transport
 *    track's label and a per-turn segmentation id are both real identities and do merge.
 *  - It never touches drafts. `publishPending` and `clearPending` pass through untouched: a
 *    draft can still be retracted or repainted, and folding one into a settled block would make
 *    that unrepresentable.
 *  - It never merges across a language change, so a per-chunk language misdetection stays
 *    visible as its own row instead of being hidden inside a longer block.
 *
 * THE ONE SEMANTIC CHANGE, STATED PLAINLY: a confirmed row's TEXT can now grow after it is
 * published. It was already true that a confirmed row's SPEAKER could change (that is what
 * `rename` does), and both travel by the same upsert. A consumer that treats the first sight of
 * a completed segment as final will see the same segment id again with more words on it.
 */
import type { ChunkSegment, ChunkedTranscriberCallbacks } from './chunked-transcriber.js';

/** Silence longer than this between two finals is a pause the speaker took, not a cut we made. */
const GAP_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_STITCH_GAP_MS) || 1500);
/** A finished sentence followed by a real pause ends the block. A finished sentence run straight
 *  into the next one does not — that is what continuous speech sounds like, and breaking there
 *  would reintroduce the fragmentation one punctuation mark at a time. */
const SENTENCE_BREAK_GAP_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_STITCH_SENTENCE_GAP_MS) || 700);
/** Caps, so a monologue does not become one unreadable row. */
const MAX_BLOCK_MS = Number((typeof process !== 'undefined' && process.env?.VEXA_STITCH_MAX_MS) || 45_000);
const MAX_BLOCK_CHARS = Number((typeof process !== 'undefined' && process.env?.VEXA_STITCH_MAX_CHARS) || 700);
/** The label that names nobody. Equality on it is not identity, so it never merges. */
const PROVISIONAL_SPEAKER = 'Speaker';

/** Sentence-final punctuation in every script the corpus contains. An ASCII-only test reads a
 *  Russian meeting as one unbroken fragment — the completeness harness already paid for that. */
const SENTENCE_END = '.?!…。？！‽';
const TRAILING_CLOSERS = '"\'»”’)]}';

function endsSentence(text: string): boolean {
  let t = text.trimEnd();
  while (t && TRAILING_CLOSERS.includes(t[t.length - 1])) t = t.slice(0, -1).trimEnd();
  return t.length > 0 && SENTENCE_END.includes(t[t.length - 1]);
}

/** Concatenate without inventing or deleting a character of speech. */
function joinText(parts: string[]): string {
  let out = '';
  for (const raw of parts) {
    const p = raw.trim();
    if (!p) continue;
    if (!out) out = p;
    else if ('-—'.includes(out[out.length - 1]) || ',.;:!?'.includes(p[0])) out += p;
    else out += ' ' + p;
  }
  return out.replace(/\s+/g, ' ').trim();
}

interface Block {
  /** The id the ROW is published under — the first member's own id. */
  id: string;
  name: string;
  members: ChunkSegment[];
}

function row(b: Block): ChunkSegment {
  const first = b.members[0];
  return {
    segmentId: b.id,
    text: joinText(b.members.map((m) => m.text)),
    startMs: first.startMs,
    endMs: b.members[b.members.length - 1].endMs,
    language: first.language,
  };
}

function blockText(b: Block): string {
  return joinText(b.members.map((m) => m.text));
}

export interface StitchOptions {
  /** OFF restores the pre-stitch publish boundary exactly — the kill switch, so a regression
   *  found in production is one environment variable, not a rollback. */
  enabled?: boolean;
  log?: (msg: string) => void;
}

/**
 * Wrap a callback set so its confirmed finals arrive stitched. Everything the lane does above
 * this seam is unchanged: it still cuts where it cuts, still attributes per word, still
 * retracts and repaints. This only changes how many rows those decisions become.
 */
export function stitchPublishBoundary(
  cb: ChunkedTranscriberCallbacks,
  opts: StitchOptions = {},
): ChunkedTranscriberCallbacks {
  const enabled = opts.enabled ?? (typeof process === 'undefined'
    || process.env?.VEXA_PUBLISH_STITCH !== '0');
  if (!enabled) return cb;

  /** The block still accepting new finals. Merges are strictly forward in time, so one is enough. */
  let open: Block | null = null;
  /** Every original segment id → the block that swallowed it, so a later rename of the lane's own
   *  ids can be translated into a rename of the rows that actually exist. */
  const byOrig = new Map<string, Block>();

  const canMerge = (b: Block, name: string, seg: ChunkSegment): boolean => {
    if (b.name !== name) return false;
    // "Speaker" is a refusal, not a person: two spans we could not attribute are not one speaker.
    if (!name || name === PROVISIONAL_SPEAKER) return false;
    if (b.members[0].language !== seg.language) return false;
    const end = b.members[b.members.length - 1].endMs;
    const gap = seg.startMs - end;
    if (gap > GAP_MS) return false;
    if (gap >= SENTENCE_BREAK_GAP_MS && endsSentence(blockText(b))) return false;
    if (seg.endMs - b.members[0].startMs > MAX_BLOCK_MS) return false;
    if (blockText(b).length + seg.text.length + 1 > MAX_BLOCK_CHARS) return false;
    return true;
  };

  return {
    ...cb,

    publish(speaker: string, confirmed: ChunkSegment[], pending: ChunkSegment[]): void {
      if (confirmed.length === 0) { cb.publish(speaker, confirmed, pending); return; }
      // Absorb each final into the open block or start a new one. `touched` is the ordered set of
      // rows this call changed — usually one.
      const touched: Block[] = [];
      for (const seg of confirmed) {
        if (open && canMerge(open, speaker, seg)) {
          open.members.push(seg);
        } else {
          open = { id: seg.segmentId, name: speaker, members: [seg] };
        }
        byOrig.set(seg.segmentId, open);
        if (touched[touched.length - 1] !== open) touched.push(open);
      }
      // The pending tail rides with the LAST row, exactly as the lane attaches it to the last
      // name-group: pending is a full-replace block, so hanging it on an earlier row would have
      // the next publish retract it.
      touched.forEach((b, i) => {
        cb.publish(b.name, [row(b)], i === touched.length - 1 ? pending : []);
      });
    },

    // Drafts are untouched, in both directions.
    publishPending(speaker: string, segments: ChunkSegment[]): void {
      cb.publishPending(speaker, segments);
    },
    clearPending(speaker: string): void {
      cb.clearPending(speaker);
    },

    /**
     * A repaint arrives naming the lane's OWN segment ids, most of which were never published on
     * their own. Translate it into a repaint of the rows that exist.
     *
     * A rename can also cut through a block: a block may hold pieces from two turns that shared a
     * name, and a later rename may claim only one of them. The block is then split along the runs
     * that are and are not being renamed — the first run keeps the row's id (so the row the
     * customer has shrinks to its remaining words), and each later run becomes its own row under
     * its first member's id.
     */
    rename(oldSpeaker: string, newSpeaker: string, segments: ChunkSegment[]): void {
      const wanted = new Set(segments.map((s) => s.segmentId));
      // Preserve the order blocks were created in, and visit each affected block once.
      const affected: Block[] = [];
      for (const s of segments) {
        const b = byOrig.get(s.segmentId);
        if (b && !affected.includes(b)) affected.push(b);
      }
      if (affected.length === 0) { cb.rename(oldSpeaker, newSpeaker, segments); return; }

      for (const b of affected) {
        // Maximal runs of same renamed-ness, in time order.
        const runs: Array<{ renamed: boolean; members: ChunkSegment[] }> = [];
        for (const m of b.members) {
          const r = wanted.has(m.segmentId);
          const last = runs[runs.length - 1];
          if (last && last.renamed === r) last.members.push(m);
          else runs.push({ renamed: r, members: [m] });
        }
        const subs: Block[] = runs.map((r, i) => ({
          // The first run keeps the row the customer already has; later runs are new rows.
          id: i === 0 ? b.id : r.members[0].segmentId,
          name: r.renamed ? newSpeaker : b.name,
          members: r.members,
        }));
        for (const sub of subs) for (const m of sub.members) byOrig.set(m.segmentId, sub);
        if (open === b) open = subs[subs.length - 1];

        for (let i = 0; i < subs.length; i++) {
          const sub = subs[i];
          if (runs[i].renamed) cb.rename(oldSpeaker, newSpeaker, [row(sub)]);
          // A retained run still has to be re-emitted: its row's text just lost the words that
          // moved to the other speaker. Publishing it as confirmed with no pending is the same
          // upsert the rename uses, and it must not carry a pending block it does not own.
          else cb.publish(sub.name, [row(sub)], []);
        }
        if (subs.length > 1) {
          opts.log?.(`[publish-stitch] rename split block ${b.id} into ${subs.length} row(s)`);
        }
      }
    },
  };
}
