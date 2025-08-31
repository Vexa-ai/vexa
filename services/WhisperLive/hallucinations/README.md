### Hallucinations: How to Reproduce, Collect, and Filter

Use this guide to reproduce Whisper hallucinations, collect examples in your target language, and contribute phrases that the filter can suppress.

### Prerequisites

- Local stack running (see `DEPLOYMENT.md`), or dev stack via `make all`
- Jupyter kernel for `vexa/tests.ipynb`

### Steps (using `tests.ipynb`)

1) Start a bot in the target language and stay silent
   - In `tests.ipynb`, request a bot with `language` set to your target (e.g., `es`, `pt`, `ru`, `en`).
2) Add background noise to trick VAD
   - Play continuous background noise (e.g., “busy cafe noise” on Spotify) from your phone near the mic to trigger VAD without real speech.
3) Watch transcripts accumulate
   - In the notebook, poll transcripts every second; leave it running for several minutes.
4) Collect hallucinations
   - Extract suspected hallucinations from WhisperLive logs (from docker compose logs) and from the transcript stream.
   - Save them to a new file named after your language code in this folder, e.g., `es.txt`, `pt.txt`, `ru.txt`, `en.txt`.
5) Rebuild and restart
   - Stop services: `docker compose down`
   - Rebuild and start: `make all`
6) Verify filtering
   - Repeat steps 1–3 and confirm the added phrases are now suppressed or significantly reduced.

### Tips for Quality Contributions

- Keep lines short, one hallucination phrase per line, it's an exact string match filter!

### Folder Structure

- `en.txt`, `es.txt`, `pt.txt`, `ru.txt`: language-specific hallucination phrase lists (one per line).

### Notes

- Filtering is language-specific; add phrases to the correct language file.
- If your language is missing, create `<lang>.txt` in this folder and submit a PR.
