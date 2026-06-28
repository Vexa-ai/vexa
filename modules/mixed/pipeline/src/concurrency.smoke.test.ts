/**
 * concurrency.smoke — overlapping/queued speakers must not erase each other. Anna
 * is speaking; Bob briefly interjects (a concurrent hint) and ends. Anna's hint
 * turn must stay OPEN (not closed by Bob), so a later turn still in Anna's window
 * resolves to Anna rather than being lost to Bob or going provisional.
 *
 * (Old serial model: Bob's hint closed Anna's turn → Anna's later speech was lost.)
 */
import { ClusterNameBinder } from './index';

const b = new ClusterNameBinder({});
b.recordHint({ name: 'Anna', tMs: 1000, kind: 'dom-outline' });               // Anna speaking
b.recordHint({ name: 'Bob',  tMs: 2000, kind: 'dom-outline' });               // Bob interjects (concurrent)
b.recordHint({ name: 'Bob',  tMs: 2500, kind: 'dom-outline', isEnd: true });  // Bob done; Anna continues

// A turn well after Bob's brief interjection, still inside Anna's open window.
const r = b.resolve({ clusterId: 's1', tStartMs: 4500, tEndMs: 5000 });
console.log(`turn after Bob's interjection → ${r.speakerName} (${r.source})`);
const ok = r.speakerName === 'Anna';
console.log(ok
  ? "✅ PASS — concurrent speaker not lost: Anna survives Bob's overlap"
  : '❌ FAIL — Anna erased by an overlapping speaker (serial-collapse)');
process.exit(ok ? 0 : 1);
