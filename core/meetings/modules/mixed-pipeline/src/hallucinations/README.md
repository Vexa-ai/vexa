# mixed-pipeline/src/hallucinations

Phrase list of known Whisper hallucinations for the **mixed** lane — the MEDIA-ARTIFACT family only.
`hallucination-gate.ts` loads `mixed.txt` (one phrase per line, `#` comments ignored) and drops
exact matches. The build copies this dir to `dist/hallucinations` so it ships.

One file, and it is deliberately narrower than the gmeet lane's:

- **`mixed.txt`** — a **strict subset** of `../../../gmeet-pipeline/src/hallucinations/*.txt`,
  selected by one rule: a line stays only if it *cannot plausibly be said in a meeting*
  (subtitle credits, YouTube-outro boilerplate, and similar media artefacts). The mixed lane
  carries real meeting audio from every participant, so filtering an ordinary phrase like
  "thank you" would delete speech.

`hallucinations-are-subset.test.ts` pins the subset relation: every line here must still exist in
the gmeet lists. Add a phrase there first, then narrow it into this file — never the other way
round, and never hand-edit a phrase into `mixed.txt` that a person could say out loud.
