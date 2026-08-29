import type { TranscriptionSegment } from '@vexa/transcribe-whisper';
import {
  detectTeamsWordContest,
  markTeamsWordContests,
  type TeamsContestRow,
  type TeamsContestSubmission,
  type TeamsRoutedSpan,
} from './teams-contested-word-marker.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

const words = (text: string, first = 0, step = 0.4): TranscriptionSegment[] => [{
  text,
  start: first,
  end: first + text.split(/\s+/).length * step,
  words: text.split(/\s+/).map((word, index) => ({
    word,
    start: first + index * step,
    end: first + (index + 0.8) * step,
    probability: 0.99,
  })),
}];

const left: TeamsContestRow = {
  csrc: 201,
  sourceKey: 'csrc-201:1',
  segmentId: 'left',
  text: 'ask amazing amazing like really good',
  startMs: 1000,
  endMs: 5000,
  completed: true,
};
const right: TeamsContestRow = {
  csrc: 840,
  sourceKey: 'csrc-840:1',
  segmentId: 'right',
  text: 'Amazing amazing like really good answer',
  startMs: 1400,
  endMs: 5000,
  completed: true,
};
const submissions: TeamsContestSubmission[] = [
  { sourceKey: left.sourceKey, segments: words(left.text) },
  { sourceKey: right.sourceKey, segments: words(right.text) },
];
const spans: TeamsRoutedSpan[] = [
  { csrc: 201, startMs: 1000, endMs: 5000 },
  { csrc: 840, startMs: 1400, endMs: 5000 },
];

const contest = detectTeamsWordContest(left, right, submissions, spans);
check('shared routed PCM + near word times + same phrase produces one unresolved contest',
  contest?.contestedText === 'amazing amazing like really good', JSON.stringify(contest));
if (contest) {
  check('only the exact duplicated words are wrapped on the named row',
    markTeamsWordContests(left, [contest])
      === 'ask [amazing amazing like really good]');
  check('the rival row carries the symmetric unresolved marker',
    markTeamsWordContests(right, [contest])
      === '[Amazing amazing like really good] answer');
}

check('same text without shared routed PCM is not a contest',
  detectTeamsWordContest(left, right, submissions, [spans[0]]) === null);

const distantRight = { ...right, text: 'one two three four five Amazing amazing like really good' };
const distantSubmissions = submissions.concat({ sourceKey: right.sourceKey, segments: words(distantRight.text) });
check('same text far apart in Whisper word time is not a contest',
  detectTeamsWordContest(left, distantRight, distantSubmissions, spans) === null);

if (failed > 0) process.exit(1);
console.log('\n✅ Teams contested-word evaluation diagnostics stay exact, symmetric, and unresolved.');
