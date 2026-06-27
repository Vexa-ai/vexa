import { SpeakerStreamManager } from './speaker-streams.js';

const seg = (text: string, end: number) => ({ start: 0, end, text });

function makeStore() {
  const store = new Map<string, { text: string; completed: boolean }>();
  const SID = 'ch-1:1';
  const mgr = new SpeakerStreamManager();
  mgr.onSegmentPending = (id, _n, text, startMs) => {
    const segId = `${id}:${Math.round(startMs)}`;
    if (text.trim() === '') { store.delete(segId); return; }
    store.set(segId, { text: text.trim(), completed: false });
  };
  mgr.onSegmentConfirmed = (id, _n, text, startMs) => {
    const segId = `${id}:${Math.round(startMs)}`;
    store.set(segId, { text: text.trim(), completed: true });
  };
  mgr.addSpeaker(SID, 'Speaker');
  return { mgr, store, SID };
}

function report(label: string, store: Map<string, { text: string; completed: boolean }>) {
  console.log('--- ' + label + ' ---');
  for (const [id, s] of store) console.log('   ', id, s.completed ? 'CONFIRMED' : 'PENDING ', JSON.stringify(s.text));
  const pendings = [...store.values()].filter(s => !s.completed);
  console.log('   dangling pendings:', pendings.length);
}

async function main() {
  {
    const { mgr, store, SID } = makeStore();
    mgr.handleTranscriptionResult(SID, 'And they had to', 2.0, [seg('And they had to', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'And they had to roll it back', 3.0, [seg('And they had to roll it back', 3.0)]);
    await mgr.flushSpeaker(SID, true);
    report('A: forming draft then flush', store);
    mgr.removeAll();
  }
  {
    const { mgr, store, SID } = makeStore();
    mgr.handleTranscriptionResult(SID, 'one two three', 2.0, [seg('one two', 1.0), seg('three', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'one two three four', 2.5, [seg('one two', 1.0), seg('three four', 2.5)]);
    mgr.handleTranscriptionResult(SID, 'one two three four five', 3.0, [seg('one two', 1.0), seg('three four five', 3.0)]);
    await mgr.flushSpeaker(SID, true);
    report('B: confirm then forming draft then flush', store);
    mgr.removeAll();
  }
  {
    const { mgr, store, SID } = makeStore();
    mgr.handleTranscriptionResult(SID, 'And they had to', 2.0, [seg('And they had to', 2.0)]);
    mgr.handleTranscriptionResult(SID, 'And they had to roll it back', 3.0, [seg('And they had to roll it back', 3.0)]);
    mgr.removeSpeaker(SID);
    report('C: forming draft then removeSpeaker', store);
  }
  {
    const { mgr, store, SID } = makeStore();
    mgr.handleTranscriptionResult(SID, 'hello world', 2.0);
    mgr.handleTranscriptionResult(SID, 'hello world', 2.0);
    mgr.handleTranscriptionResult(SID, 'hello world', 2.0, [seg('hello world', 2.0)]);
    await mgr.flushSpeaker(SID, true);
    report('D: dedup blocks emit then flush', store);
    mgr.removeAll();
  }
}
main();
