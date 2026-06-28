# @vexa/mixed-eval

Agentic evaluation vehicle for the mixed lane: pull a YouTube fixture (Deepgram
reference), stream a region through `@vexa/mixed-pipeline` (real pyannote + real
Whisper), and render a side-by-side, timestamp-aligned page with audio playback +
segmentation-boundary pointers.

**See [CLAUDE.md](CLAUDE.md)** for how an agent operates the evaluation
(pull → pick a region by quality → run → serve → judge segmentation).

```
node pull.mjs <youtube-url>            # → fixtures/<id>/{audio.wav, deepgram.json, meta.json}
npm run run -- --id <id> --start <s> --end <e>   # → fixtures/<id>/eval-<s>-<e>.html
npm run serve -- --id <id>             # view over http (file:// blocks the inline audio)
```

`fixtures/` is git-ignored — real transcripts are sensitive.
