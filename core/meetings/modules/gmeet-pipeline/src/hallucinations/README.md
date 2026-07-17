# gmeet-pipeline/src/hallucinations

Per-language phrase lists of known Whisper hallucinations — faint-audio artefacts the model emits as
confident text ("thank you", subtitle credits, YouTube-outro boilerplate, etc.).
`hallucination-filter.ts` loads **every** `*.txt` here (one phrase per line, `#` comments ignored)
and drops exact matches. The build copies this dir to `dist/hallucinations` so it ships.

Two kinds of file, both loaded and unioned:

- **`<lang>.txt`** — hand-curated, human-verified (e.g. `en/es/pt/ru/ja/tr`).
- **`<lang>.harvested.txt`** — **GENERATED** by `../harvest-hallucinations.ts`: it feeds
  guaranteed-non-speech audio (silence + white noise) through the real STT forcing each language, so
  every transcribed string is a hallucination by construction. Do NOT hand-edit these — re-run the
  harvester (`VEXA_TX_KEY=… npm run harvest:hallucinations`) when the STT model bumps and review the
  diff. This is the durable, comprehensive, per-model source; the hand lists are the verified floor.
