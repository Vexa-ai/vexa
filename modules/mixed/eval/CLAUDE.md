# mixed/eval — how to evaluate the mixed lane (agent guide)

You are operating an evaluation vehicle for the **mixed** transcription lane
(single mixed audio stream → pyannote **segmentation** cuts turns → shared
LocalAgreement-3 confirm → Whisper). **Clustering is OFF by design** — the lane
separates *speeches* by segmentation, not speaker *identity*. So judge the
**cut placement** (did we start/end a turn at the right instant?) and the
**transcript text**, NOT whether "S0" matches Deepgram's "S0".

Deepgram (nova-2, diarized) is the **reference** ("ground truth"). It is not
perfect, but it is the yardstick.

## The two commands

```bash
# 1. pull a fixture from a YouTube link  (audio + Deepgram reference)
DEEPGRAM_API_KEY=$(grep -o 'sk-[^ ]*\|[A-Za-z0-9]\{32,\}' /tmp/.dg.env | head -1) \
  npm run pull -- "https://www.youtube.com/watch?v=XXXX"
#   → fixtures/<id>/{audio.wav, deepgram.json, meta.json}    (fixtures/ is git-ignored)

# 2. evaluate a chosen region through our pipeline → side-by-side page
set -a; source ../../../services/vexa-desktop/.env; set +a   # our Whisper egress
npm run run -- --id <id> --start <sec> --end <sec> [--speed 3]
#   → fixtures/<id>/eval-<start>-<end>.html
```

```bash
# 3. view it — MUST be over http (file:// blocks the inline audio playback)
npm run serve -- --id <id>          # serves the latest eval-*.html + opens the browser
```

`pull` is idempotent (skips download/Deepgram if already present). `run` streams
the region through the real `ChunkedTranscriber` (real pyannote segmenter + real
Whisper, `clustering:false`) and prints frame/STT counts as a streaming check.
The page embeds the audio inline, but **double-clicking the .html (file://) will
not play it** — Chrome/Safari restrict media over `file://`. Always `npm run serve`.

## How to pick a region (the "quality" the user asks for)

After `pull`, read `fixtures/<id>/deepgram.json` → `results.utterances[]`
(`{speaker, start, end, transcript}`). Choose a 30–90 s window that exercises
the quality the user named, e.g.:

- **frequent speaker changes** → find a stretch where `speaker` flips often and
  turns are short (stresses cut latency / over- and under-segmentation).
- **monologue** → one speaker for a long run (stresses LocalAgreement drift &
  turn-roll, should NOT fragment).
- **fast back-and-forth / interruptions** → short adjacent turns with small gaps
  (stresses speaker→speaker cuts).
- **overlap** → utterances whose `[start,end]` intervals intersect across
  speakers (pyannote emits `overlap-onset/offset`).

Pick `--start/--end` to bracket that window (a few seconds of lead-in helps).

## Reading the page

Open the HTML. Three columns:
- **timeline** (left rail): pyannote boundary ticks — green `→speaker`
  (cut/open) and red `speaker→silence` (close). Click a tick to seek the audio
  to that instant and hear whether the cut was right.
- **Deepgram (reference)** and **Vexa (ours)**: rows are positioned by time, so
  the two sides line up vertically. Click any row to play from its start.

Judge: do Vexa's cuts land where speakers actually change (green ticks aligned
with Deepgram turn edges)? Is the text faithful? Note over-cuts (one utterance
split), under-cuts (two speakers merged), and late cuts (boundary drifts past
the real turn change).

## Notes / invariants

- **fixtures/ is NEVER committed** — real transcripts are sensitive; `.gitignore`
  already excludes `fixtures/` and `node_modules/`.
- The mixed lane stays **segmentation-separated, clustering OFF** — do not turn
  clustering on to "fix" speaker labels; that is a different (dropped) feature.
- `--speed N` feeds audio N× real-time; lower it (→1) if Whisper egress can't
  keep up and the tail looks truncated.
