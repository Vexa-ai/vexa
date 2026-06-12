/**
 * Tests for post-transcription speaker mapping.
 *
 * Run: npx ts-node core/src/services/speaker-mapper.test.ts
 */

import {
  mapWordsToSpeakers,
  captionsToSpeakerBoundaries,
  TimestampedWord,
  SpeakerBoundary,
  CaptionEvent,
} from './speaker-mapper';

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string): void {
  if (condition) {
    passed++;
    console.log(`  ✓ ${message}`);
  } else {
    failed++;
    console.log(`  ✗ ${message}`);
  }
}

// ── Test 1: Basic two-speaker mapping ────────────────────────

console.log('\nTest 1: Two speakers, clean boundaries');
{
  const words: TimestampedWord[] = [
    { word: 'Hello', start: 0.0, end: 0.4 },
    { word: 'everyone', start: 0.5, end: 1.0 },
    { word: 'how', start: 1.1, end: 1.3 },
    { word: 'are', start: 1.4, end: 1.6 },
    { word: 'you', start: 1.7, end: 2.0 },
    // Speaker B starts
    { word: 'Great', start: 3.0, end: 3.3 },
    { word: 'thanks', start: 3.4, end: 3.7 },
    { word: 'for', start: 3.8, end: 4.0 },
    { word: 'asking', start: 4.1, end: 4.5 },
  ];

  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0.0, end: 2.5 },
    { speaker: 'Bob', start: 2.5, end: 5.0 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  assert(segments.length === 2, `2 segments (got ${segments.length})`);
  assert(segments[0].speaker === 'Alice', `First segment: Alice (got ${segments[0]?.speaker})`);
  assert(segments[0].text === 'Hello everyone how are you', `Alice text: "${segments[0]?.text}"`);
  assert(segments[1].speaker === 'Bob', `Second segment: Bob (got ${segments[1]?.speaker})`);
  assert(segments[1].text === 'Great thanks for asking', `Bob text: "${segments[1]?.text}"`);
}

// ── Test 2: Three speakers, rapid transitions ─────────────────

console.log('\nTest 2: Three speakers, rapid turns');
{
  const words: TimestampedWord[] = [
    { word: 'I', start: 0.0, end: 0.1 },
    { word: 'agree', start: 0.2, end: 0.5 },
    { word: 'Absolutely', start: 1.0, end: 1.5 },
    { word: 'Me', start: 2.0, end: 2.2 },
    { word: 'too', start: 2.3, end: 2.5 },
  ];

  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0.0, end: 0.8 },
    { speaker: 'Bob', start: 0.8, end: 1.8 },
    { speaker: 'Charlie', start: 1.8, end: 3.0 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  assert(segments.length === 3, `3 segments (got ${segments.length})`);
  assert(segments[0].speaker === 'Alice', `Alice: "I agree"`);
  assert(segments[1].speaker === 'Bob', `Bob: "Absolutely"`);
  assert(segments[2].speaker === 'Charlie', `Charlie: "Me too"`);
}

// ── Test 3: Word straddles speaker boundary ──────────────────

console.log('\nTest 3: Word straddles boundary — goes to speaker with more overlap');
{
  const words: TimestampedWord[] = [
    { word: 'before', start: 0.0, end: 0.5 },
    // This word spans the boundary at 1.0
    { word: 'straddling', start: 0.8, end: 1.3 },
    { word: 'after', start: 1.5, end: 2.0 },
  ];

  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0.0, end: 1.0 },
    { speaker: 'Bob', start: 1.0, end: 2.5 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  // "straddling" is 0.8-1.3: 0.2s in Alice, 0.3s in Bob → Bob wins
  assert(segments.length === 2, `2 segments (got ${segments.length})`);
  assert(segments[0].speaker === 'Alice' && segments[0].text === 'before', `Alice: "before"`);
  assert(segments[1].speaker === 'Bob' && segments[1].text === 'straddling after', `Bob: "straddling after"`);
}

// ── Test 4: Word outside all boundaries — nearest speaker ─────

console.log('\nTest 4: Word in gap between speakers — nearest wins');
{
  const words: TimestampedWord[] = [
    { word: 'hello', start: 0.0, end: 0.5 },
    // Gap: no speaker from 1.0 to 3.0
    { word: 'orphan', start: 1.5, end: 2.0 },
    { word: 'world', start: 3.5, end: 4.0 },
  ];

  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0.0, end: 1.0 },
    { speaker: 'Bob', start: 3.0, end: 5.0 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  // "orphan" at 1.5-2.0: distance to Alice end (1.0) = 0.5, distance to Bob start (3.0) = 1.0 → Alice
  assert(segments[0].speaker === 'Alice', `"orphan" attributed to Alice (nearest)`);
  assert(segments[1].speaker === 'Bob', `"world" attributed to Bob`);
}

// ── Test 5: captionsToSpeakerBoundaries with author:text events ──

console.log('\nTest 5: Caption events (author:text:timestamp) → boundaries');
{
  // Simulate real Teams caption stream:
  // Alice speaks, text grows word by word
  // Then Bob starts — author switches
  // Alice's old entry may get a refinement (discarded — same author won't re-trigger)
  // Then Alice speaks again — author switches back
  const captions: CaptionEvent[] = [
    // Alice active — text growing
    { speaker: 'Alice', text: 'Hello',           timestamp: 0.0 },
    { speaker: 'Alice', text: 'Hello everyone',   timestamp: 0.5 },
    { speaker: 'Alice', text: 'Hello everyone.',  timestamp: 1.0 },
    { speaker: 'Alice', text: 'I want to start',  timestamp: 2.0 },
    // Bob starts — author switch → Alice segment ends, Bob starts
    { speaker: 'Bob',   text: 'That',              timestamp: 5.0 },
    { speaker: 'Bob',   text: 'That is great',     timestamp: 5.5 },
    { speaker: 'Bob',   text: 'That is great news', timestamp: 6.0 },
    // Alice speaks again — author switch → Bob segment ends
    { speaker: 'Alice', text: 'Thanks',            timestamp: 10.0 },
    { speaker: 'Alice', text: 'Thanks Bob',        timestamp: 10.5 },
  ];

  const boundaries = captionsToSpeakerBoundaries(captions);

  assert(boundaries.length === 3, `3 boundaries (got ${boundaries.length})`);
  assert(boundaries[0].speaker === 'Alice' && boundaries[0].start === 0.0 && boundaries[0].end === 5.0, `Alice 0-5s`);
  assert(boundaries[1].speaker === 'Bob' && boundaries[1].start === 5.0 && boundaries[1].end === 10.0, `Bob 5-10s`);
  assert(boundaries[2].speaker === 'Alice' && boundaries[2].start === 10.0, `Alice 10s+`);

  console.log('  Boundaries:');
  for (const b of boundaries) {
    console.log(`    ${b.speaker}: ${b.start.toFixed(1)}s - ${b.end.toFixed(1)}s`);
  }
}

// ── Test 6: Realistic Teams scenario ─────────────────────────

console.log('\nTest 6: Realistic Teams conversation');
{
  // Simulated Whisper output for a two-person conversation
  const words: TimestampedWord[] = [
    // Alice speaks
    { word: 'Good', start: 0.0, end: 0.3 },
    { word: 'morning', start: 0.3, end: 0.7 },
    { word: 'everyone', start: 0.8, end: 1.2 },
    { word: 'revenue', start: 1.5, end: 2.0 },
    { word: 'is', start: 2.0, end: 2.2 },
    { word: 'up', start: 2.2, end: 2.4 },
    { word: 'fifteen', start: 2.5, end: 2.9 },
    { word: 'percent', start: 3.0, end: 3.4 },
    // Bob speaks
    { word: 'Those', start: 5.0, end: 5.3 },
    { word: 'numbers', start: 5.3, end: 5.7 },
    { word: 'are', start: 5.8, end: 6.0 },
    { word: 'impressive', start: 6.0, end: 6.5 },
    // Alice responds
    { word: 'Thanks', start: 8.0, end: 8.3 },
    { word: 'Bob', start: 8.4, end: 8.6 },
  ];

  // Caption boundaries from Teams
  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice (Guest)', start: 0.0, end: 4.5 },
    { speaker: 'Bob (Guest)', start: 4.5, end: 7.5 },
    { speaker: 'Alice (Guest)', start: 7.5, end: 10.0 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  assert(segments.length === 3, `3 segments (got ${segments.length})`);
  assert(segments[0].speaker === 'Alice (Guest)' && segments[0].wordCount === 8, `Alice: 8 words`);
  assert(segments[0].text === 'Good morning everyone revenue is up fifteen percent', `Alice text correct`);
  assert(segments[1].speaker === 'Bob (Guest)' && segments[1].wordCount === 4, `Bob: 4 words`);
  assert(segments[2].speaker === 'Alice (Guest)' && segments[2].text === 'Thanks Bob', `Alice reply: "Thanks Bob"`);

  console.log('\n  Attributed segments:');
  for (const seg of segments) {
    console.log(`    [${seg.speaker}] ${seg.start.toFixed(1)}s-${seg.end.toFixed(1)}s: "${seg.text}"`);
  }
}

// ── Test 7: Caption delay — boundaries shifted 1.5s late ──────

console.log('\nTest 7: Caption delay (1.5s) — boundaries arrive late');
{
  // Real speech timing
  const words: TimestampedWord[] = [
    // Alice speaks 0-4s
    { word: 'Let', start: 0.0, end: 0.2 },
    { word: 'me', start: 0.2, end: 0.4 },
    { word: 'start', start: 0.5, end: 0.8 },
    { word: 'with', start: 0.9, end: 1.1 },
    { word: 'the', start: 1.2, end: 1.3 },
    { word: 'results', start: 1.4, end: 1.9 },
    { word: 'revenue', start: 2.0, end: 2.5 },
    { word: 'is', start: 2.6, end: 2.7 },
    { word: 'up', start: 2.8, end: 3.0 },
    // Bob speaks 4-8s
    { word: 'That', start: 4.2, end: 4.5 },
    { word: 'is', start: 4.5, end: 4.7 },
    { word: 'great', start: 4.8, end: 5.1 },
    { word: 'news', start: 5.2, end: 5.5 },
    { word: 'what', start: 5.8, end: 6.0 },
    { word: 'about', start: 6.1, end: 6.4 },
    { word: 'Europe', start: 6.5, end: 7.0 },
    // Alice speaks again 8-11s
    { word: 'Europe', start: 8.5, end: 8.9 },
    { word: 'grew', start: 9.0, end: 9.3 },
    { word: 'twenty', start: 9.4, end: 9.7 },
    { word: 'percent', start: 9.8, end: 10.2 },
  ];

  // Captions arrive 1.5s LATE — this is the real-world Teams delay
  const CAPTION_DELAY = 1.5;
  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0.0 + CAPTION_DELAY, end: 4.0 + CAPTION_DELAY },
    { speaker: 'Bob', start: 4.0 + CAPTION_DELAY, end: 8.0 + CAPTION_DELAY },
    { speaker: 'Alice', start: 8.0 + CAPTION_DELAY, end: 12.0 + CAPTION_DELAY },
  ];
  // Boundaries: Alice 1.5-5.5, Bob 5.5-9.5, Alice 9.5-13.5

  const segments = mapWordsToSpeakers(words, speakers);

  console.log('  Caption delay: 1.5s');
  console.log('  Boundaries: Alice 1.5-5.5s, Bob 5.5-9.5s, Alice 9.5-13.5s');
  for (const seg of segments) {
    console.log(`    [${seg.speaker}] ${seg.start.toFixed(1)}s-${seg.end.toFixed(1)}s: "${seg.text}"`);
  }

  // With 1.5s delay, first few words of each speaker may bleed to previous
  // "Let me start" at 0.0-0.8s falls BEFORE Alice boundary (1.5s) — attributed to nearest (Alice)
  // "That is" at 4.2-4.7s falls within Alice boundary (1.5-5.5) — WRONG (should be Bob)
  // This is the known caption delay issue
  assert(segments.length >= 2, `At least 2 segments (got ${segments.length})`);

  // Count misattributed words
  const bobWords = segments.filter(s => s.speaker === 'Bob').reduce((sum, s) => sum + s.wordCount, 0);
  const aliceWords = segments.filter(s => s.speaker === 'Alice').reduce((sum, s) => sum + s.wordCount, 0);
  console.log(`  Alice words: ${aliceWords}, Bob words: ${bobWords}`);
  console.log(`  Expected: Alice=13, Bob=7. Caption delay shifts ~2 words from Bob→Alice`);
  assert(aliceWords > 10, `Alice got most words (${aliceWords})`);
  assert(bobWords > 0, `Bob got some words (${bobWords})`);
}

// ── Test 8: Long conversation with many transitions ───────────

console.log('\nTest 8: 5-speaker meeting simulation');
{
  // Simulate a meeting with 5 speakers over 60 seconds
  const speakers: SpeakerBoundary[] = [
    { speaker: 'Alice', start: 0, end: 10 },
    { speaker: 'Bob', start: 10, end: 18 },
    { speaker: 'Charlie', start: 18, end: 25 },
    { speaker: 'Alice', start: 25, end: 35 },
    { speaker: 'Diana', start: 35, end: 42 },
    { speaker: 'Eve', start: 42, end: 50 },
    { speaker: 'Bob', start: 50, end: 60 },
  ];

  // Generate words every 0.3s
  const words: TimestampedWord[] = [];
  for (let t = 0; t < 60; t += 0.3) {
    words.push({ word: `w${Math.floor(t * 10)}`, start: t, end: t + 0.25 });
  }

  const segments = mapWordsToSpeakers(words, speakers);

  const speakerNames = new Set(segments.map(s => s.speaker));
  assert(speakerNames.size === 5, `5 unique speakers (got ${speakerNames.size}: ${[...speakerNames].join(', ')})`);
  assert(segments.length >= 7, `At least 7 segments (got ${segments.length})`);
  assert(segments.reduce((sum, s) => sum + s.wordCount, 0) === words.length, `All ${words.length} words attributed`);
  console.log(`  ${words.length} words → ${segments.length} segments across ${speakerNames.size} speakers`);
}

// ── Test 9: Single speaker (Google Meet equivalent) ───────────

console.log('\nTest 7: Single speaker — all words attributed');
{
  const words: TimestampedWord[] = [
    { word: 'Hello', start: 0.0, end: 0.4 },
    { word: 'world', start: 0.5, end: 0.9 },
    { word: 'test', start: 1.0, end: 1.3 },
  ];

  const speakers: SpeakerBoundary[] = [
    { speaker: 'Speaker-0', start: 0.0, end: 5.0 },
  ];

  const segments = mapWordsToSpeakers(words, speakers);

  assert(segments.length === 1, `1 segment`);
  assert(segments[0].speaker === 'Speaker-0' && segments[0].wordCount === 3, `All 3 words to Speaker-0`);
}

// ── Summary ──────────────────────────────────────────────────

console.log(`\n${'='.repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
