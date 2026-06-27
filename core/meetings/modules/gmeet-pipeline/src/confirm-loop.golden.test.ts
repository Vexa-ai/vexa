/**
 * Golden — the gmeet LocalAgreement confirm loop's PENDING-draft stability (characterization).
 *
 * Pins the invariant a live draft must hold: for ONE segment_id, the pending text only GROWS.
 * It must never flip between the full window text and a short tail fragment under the SAME id —
 * which a consumer that upserts-by-id (the terminal) renders as a visible "output → lost →
 * reappear" flicker. The flicker is introduced HERE (not downstream): `handleTranscriptionResult`
 * must not publish two conflicting pending drafts (a tail AND the whole) per submission.
 *
 * Model-free: a SCRIPTED sequence of Whisper results drives `handleTranscriptionResult` directly
 * (no audio, no timers, no model); we record every `onSegmentPending` emission and assert per-id
 * monotonic growth. Mirrors mixed-pipeline/src/confirm-loop.golden.test.ts for the per-channel lane.
 *
 *   tsx src/confirm-loop.golden.test.ts
 */
import { SpeakerStreamManager } from './speaker-streams.js';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}

const seg = (text: string, end: number) => ({ start: 0, end, text });

// ── Scenario 1: a forming draft must never flicker full↔fragment ──────────────────────────────
// Whisper re-returns the whole window each pass as ONE growing segment (the real counting shape).
// The word-prefix matches the previous submission but does NOT map to a COMPLETE earlier Whisper
// segment, so the confirm path never fires → the engine falls through to the whole-draft emit.
// Pre-fix it ALSO emits the tail (words past the prefix) under the same id → the flicker.
{
  const SID = 'ch-1:1';
  const mgr = new SpeakerStreamManager();
  const pendings: { id: string; text: string }[] = [];
  mgr.onSegmentPending = (speakerId, _name, text, startMs) =>
    pendings.push({ id: `${speakerId}:${Math.round(startMs)}`, text: text.trim() });
  mgr.addSpeaker(SID, 'Speaker');

  const SCRIPT = [
    '39, 40, 41, 42',
    '39, 40, 41, 42, 43, 44',
    '39, 40, 41, 42, 43, 44, 45, 46',
  ];
  for (const text of SCRIPT) mgr.handleTranscriptionResult(SID, text, 2.0, [seg(text, 2.0)]);
  mgr.removeAll();

  const byId = new Map<string, string[]>();
  for (const p of pendings) {
    if (!byId.has(p.id)) byId.set(p.id, []);
    byId.get(p.id)!.push(p.text);
  }
  console.log('  pending emissions per id:');
  for (const [id, ts] of byId) console.log('    ', id, '→', JSON.stringify(ts));

  ok(byId.size > 0, 'the forming draft produced at least one pending emission');
  for (const [id, texts] of byId) {
    // An empty pending is the turn-close CLEAR (the stale-pending finalize emits "" to delete the
    // draft row — see Scenario 4); it's a delete, never flicker. Legit only at the TAIL: a draft that
    // clears and then REAPPEARS non-empty under the same id IS flicker, so we still reject that.
    let cleared = false;
    for (let i = 1; i < texts.length; i++) {
      const prev = texts[i - 1], cur = texts[i];
      if (cur === '') { cleared = true; continue; }
      ok(!cleared, `[${id}] no draft reappears after a clear (mid-stream clear-then-reappear is flicker): ${JSON.stringify(cur)}`);
      ok(
        cur === prev || cur.startsWith(prev),
        `[${id}] pending grows monotonically (no full↔fragment flicker): ${JSON.stringify(prev)} → ${JSON.stringify(cur)}`,
      );
    }
  }
}

// ── Scenario 2: removing the redundant tail emit must NOT break confirmation ───────────────────
// When the word-prefix DOES cover a complete earlier Whisper segment, that segment confirms and is
// published via onSegmentConfirmed. The fix only drops the duplicate pending emit — confirmation is
// untouched. This guards against over-correcting into a no-confirm regression.
{
  const SID = 'ch-2:1';
  const mgr = new SpeakerStreamManager();
  const confirmed: string[] = [];
  mgr.onSegmentConfirmed = (_id, _name, text) => confirmed.push(text.trim());
  mgr.onSegmentPending = () => { /* not under test here */ };
  mgr.addSpeaker(SID, 'Speaker');

  // Two consecutive submissions sharing the leading segment "one two" (a complete Whisper segment
  // within the stable word-prefix) → "one two" confirms.
  mgr.handleTranscriptionResult(SID, 'one two three four', 2.0, [seg('one two', 1.0), seg('three four', 2.0)]);
  mgr.handleTranscriptionResult(SID, 'one two three four five', 2.5, [seg('one two', 1.0), seg('three four five', 2.5)]);
  mgr.removeAll();

  console.log('  confirmed:', JSON.stringify(confirmed));
  ok(confirmed.includes('one two'), 'the stable leading segment still confirms (confirmation path intact)');
}

// ── Scenario 3: BIGGER — at scale, every submission emits AT MOST ONE pending draft per segment_id ──
// The precise invariant the fix restores: one live draft per submission, never a tail AND a whole under
// the same id. Robust to Whisper word-revisions (unlike strict text-monotonicity), so it's the real
// regression guard. A long forming utterance (count 1..60 — growing single-segment windows, the exact
// shape that double-emitted) drives ~60 submissions; pre-fix maxPerId would be 2, post-fix 1.
{
  const SID = 'ch-3:1';
  const mgr = new SpeakerStreamManager();
  let perCall: string[] = [];
  const allPending: { id: string; text: string }[] = [];
  mgr.onSegmentPending = (sid, _n, text, startMs) => {
    const id = `${sid}:${Math.round(startMs)}`;
    perCall.push(id);
    allPending.push({ id, text: text.trim() });
  };
  mgr.addSpeaker(SID, 'Speaker');

  let submissions = 0, maxPerIdPerCall = 0;
  const words: string[] = [];
  for (let i = 1; i <= 60; i++) {
    words.push(String(i));
    const text = words.join(', ');
    perCall = [];
    mgr.handleTranscriptionResult(SID, text, i, [seg(text, i)]);
    submissions++;
    const counts = new Map<string, number>();
    for (const id of perCall) counts.set(id, (counts.get(id) ?? 0) + 1);
    for (const c of counts.values()) maxPerIdPerCall = Math.max(maxPerIdPerCall, c);
  }
  mgr.removeAll();

  // per-id monotonic growth (counting doesn't revise, so prefix-monotonic must also hold)
  const byId = new Map<string, string[]>();
  for (const p of allPending) {
    if (!byId.has(p.id)) byId.set(p.id, []);
    const a = byId.get(p.id)!;
    if (a[a.length - 1] !== p.text) a.push(p.text);
  }
  // A trailing empty pending is the turn-close CLEAR (finalize delete — Scenario 4), not flicker.
  // Skip it; but a non-empty draft REAPPEARING after a clear under the same id IS a violation.
  let monotonicViolations = 0;
  for (const texts of byId.values()) {
    let cleared = false;
    for (let k = 1; k < texts.length; k++) {
      if (texts[k] === '') { cleared = true; continue; }
      if (cleared || !(texts[k] === texts[k - 1] || texts[k].startsWith(texts[k - 1]))) monotonicViolations++;
    }
  }

  console.log(`  scale: ${submissions} submissions · ${byId.size} segment_id(s) · maxPendingPerId/call=${maxPerIdPerCall}`);
  ok(submissions >= 50, `bigger fixture drove a long forming session (${submissions} submissions)`);
  ok(maxPerIdPerCall <= 1, `every submission emits AT MOST ONE pending draft per segment_id (max=${maxPerIdPerCall}) — no duplicate tail+whole`);
  ok(monotonicViolations === 0, `pending text grows monotonically across the whole session (${monotonicViolations} flicker violations)`);
}

// ── Scenario 4: TURN-CLOSE must FINALIZE the outstanding pending draft (no STALE pending lingers) ──
// The live "stale pending" bug: a speaker forms a PENDING draft, then the turn ends (silence gap,
// glow/speaker change, flush, removeSpeaker) — and the in-progress draft is left DANGLING as a
// `completed:false` segment that never confirms. (Live evidence: a meeting's feed carried a stale
// `"And they had to roll it back…"` pending under speaker "Speaker" that never finalized after the
// speaker changed.) The contract a consumer upserting-by-id relies on: a closed turn leaves NO
// `completed:false` for that speaker/window — its draft was either FINALIZED (re-emitted confirmed
// under the SAME id so the upsert replaces it) or explicitly CLEARED (onSegmentPending "" ).
//
// We model the consumer exactly like the real gmeet-pipeline (segOf) + terminal upsert-by-id:
// keyed by `${speakerId}:${round(startMs)}`, pending writes completed:false, confirmed writes
// completed:true, empty pending text DELETES the row. After turn-close NO row may remain pending.
{
  const seg2 = (text: string, end: number) => ({ start: 0, end, text });
  type Row = { text: string; completed: boolean };
  const buildStore = (mgr: SpeakerStreamManager) => {
    const store = new Map<string, Row>();
    mgr.onSegmentPending = (id, _n, text, startMs) => {
      const segId = `${id}:${Math.round(startMs)}`;
      if (text.trim() === '') { store.delete(segId); return; }      // empty ⇒ clear the draft row
      store.set(segId, { text: text.trim(), completed: false });
    };
    mgr.onSegmentConfirmed = (id, _n, text, startMs) => {
      const segId = `${id}:${Math.round(startMs)}`;
      store.set(segId, { text: text.trim(), completed: true });     // confirmed replaces same id
    };
    return store;
  };
  const danglingPendings = (store: Map<string, Row>) => [...store.values()].filter((s) => !s.completed);

  // 4a — forming draft then FLUSH (the exact live case): a draft forms, the turn flushes.
  {
    const SID = 'ch-4a:1';
    const mgr = new SpeakerStreamManager();
    const store = buildStore(mgr);
    const confirmed: string[] = [];
    const prevConfirmed = mgr.onSegmentConfirmed!;
    mgr.onSegmentConfirmed = (id, n, text, a, b, c) => { confirmed.push(text.trim()); prevConfirmed(id, n, text, a, b, c); };
    mgr.addSpeaker(SID, 'Speaker');

    mgr.handleTranscriptionResult(SID, 'And they had to', 2.0, [seg2('And they had to', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'And they had to roll it back', 3.0, [seg2('And they had to roll it back', 3.0)]);
    await mgr.flushSpeaker(SID, true);   // turn closes
    mgr.removeAll();

    console.log('  4a store after flush:', JSON.stringify([...store.entries()]));
    ok(confirmed.includes('And they had to roll it back'), '4a flush FINALIZED the outstanding draft via onSegmentConfirmed');
    ok(danglingPendings(store).length === 0, `4a NO lingering completed:false after flush (got ${danglingPendings(store).length})`);
  }

  // 4b — forming draft then removeSpeaker (turn fully removed, e.g. participant leaves).
  {
    const SID = 'ch-4b:1';
    const mgr = new SpeakerStreamManager();
    const store = buildStore(mgr);
    const confirmed: string[] = [];
    const prevConfirmed = mgr.onSegmentConfirmed!;
    mgr.onSegmentConfirmed = (id, n, text, a, b, c) => { confirmed.push(text.trim()); prevConfirmed(id, n, text, a, b, c); };
    mgr.addSpeaker(SID, 'Speaker');

    mgr.handleTranscriptionResult(SID, 'And they had to', 2.0, [seg2('And they had to', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'And they had to roll it back', 3.0, [seg2('And they had to roll it back', 3.0)]);
    mgr.removeSpeaker(SID);   // turn removed

    console.log('  4b store after removeSpeaker:', JSON.stringify([...store.entries()]));
    ok(confirmed.includes('And they had to roll it back'), '4b removeSpeaker FINALIZED the outstanding draft');
    ok(danglingPendings(store).length === 0, `4b NO lingering completed:false after removeSpeaker (got ${danglingPendings(store).length})`);
  }

  // 4c — confirm a prefix, THEN a new forming draft, then flush. The confirmed prefix stays; the
  //      forming tail must NOT dangle (the windowStartMs-advanced id divergence is the root cause).
  {
    const SID = 'ch-4c:1';
    const mgr = new SpeakerStreamManager();
    const store = buildStore(mgr);
    mgr.addSpeaker(SID, 'Speaker');

    mgr.handleTranscriptionResult(SID, 'one two three', 2.0, [seg2('one two', 1.0), seg2('three', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'one two three four', 2.5, [seg2('one two', 1.0), seg2('three four', 2.5)]);
    mgr.handleTranscriptionResult(SID, 'one two three four five', 3.0, [seg2('one two', 1.0), seg2('three four five', 3.0)]);
    await mgr.flushSpeaker(SID, true);
    mgr.removeAll();

    console.log('  4c store after flush:', JSON.stringify([...store.entries()]));
    ok([...store.values()].some((s) => s.completed && s.text === 'one two'), '4c the confirmed prefix is preserved');
    ok(danglingPendings(store).length === 0, `4c NO lingering completed:false after flush (got ${danglingPendings(store).length})`);
  }

  // 4d — the live single-pending edge still works: while the turn is OPEN, the forming draft IS shown
  //      as the one pending row (we must not break the live draft by over-eagerly clearing it).
  {
    const SID = 'ch-4d:1';
    const mgr = new SpeakerStreamManager();
    const store = buildStore(mgr);
    mgr.addSpeaker(SID, 'Speaker');

    mgr.handleTranscriptionResult(SID, 'still forming here', 2.0, [seg2('still forming here', 2.0)]);
    console.log('  4d store mid-turn:', JSON.stringify([...store.entries()]));
    ok(danglingPendings(store).length === 1, '4d the OPEN turn still shows exactly one live pending draft');
    mgr.removeAll();
  }
}

console.log(`\n${checks} checks passed`);
