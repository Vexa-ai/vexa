#!/usr/bin/env node
// read-redis-transcript — turn a `redis-cli XRANGE transcription_segments <min> +` dump (on stdin)
// into the `{ segments:[…] }` shape `analyze.mjs` scores (via TRANSCRIPT_FILE). This is the read
// half of the O6 Meet-leg harness: the STANDALONE v0.12 bot publishes transcript.v1 to redis as
// `XADD transcription_segments * payload {"type":"transcription", ...segment}` (one segment per
// entry, fields spread at top level — see services/bot/src/adapters/transcript-redis.ts), so there
// is no gateway meeting-record to fetch. We pick out exactly those entries.
//
// Distinguishing the carved bot's entries from the legacy collector wire format: the bot spreads
// the segment at top level (a top-level `text`) and carries NO `token`; the legacy producer nests
// `"segments":[…]` under a JWT `"token"`. So: type==transcription AND has `text` AND no `token`.
//
//   ssh bbb "docker exec vexa-redis-1 redis-cli XRANGE transcription_segments $SINCE +" \
//     | node src/read-redis-transcript.mjs > transcript.json
//   TRANSCRIPT_FILE=transcript.json node src/analyze.mjs google_meet <native_id>
//
// In piped (non-tty) mode redis-cli prints each array element on its own line, so each payload
// JSON lands on a line of its own; we scan every line for a parseable transcription object. Robust
// to the id/field-name lines around it (they don't parse as a transcription object).

let raw = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) raw += chunk;

const segments = [];
const seen = new Set();
for (let line of raw.split('\n')) {
  line = line.trim();
  // redis-cli may wrap the value in quotes and backslash-escape it; unwrap a single outer layer.
  if (line.startsWith('"') && line.endsWith('"')) {
    try { line = JSON.parse(line); } catch { /* not a quoted string — use as-is */ }
  }
  if (typeof line !== 'string' || line[0] !== '{') continue;
  let o;
  try { o = JSON.parse(line); } catch { continue; }
  if (!o || o.type !== 'transcription' || o.token || typeof o.text !== 'string') continue;
  // Dedup on (start,end,text) — a segment can be re-published as it confirms (mutable → final).
  const key = `${o.start}|${o.end}|${o.text}`;
  if (seen.has(key)) continue;
  seen.add(key);
  // Keep the fields analyze.mjs reads; drop the discriminator.
  const { type, ...seg } = o; void type;
  segments.push(seg);
}

segments.sort((a, b) => (a.start || 0) - (b.start || 0));
process.stdout.write(JSON.stringify({ segments }) + '\n');
process.stderr.write(`[read-redis-transcript] ${segments.length} transcript.v1 segment(s)\n`);
