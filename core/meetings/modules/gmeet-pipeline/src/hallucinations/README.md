# gmeet-pipeline/src/hallucinations

Per-language phrase lists (`en/es/pt/ru.txt`) of known Whisper hallucinations —
faint-audio artefacts the model emits as confident text ("thank you", subtitle
credits, etc.). `hallucination-filter.ts` loads them (one phrase per line, `#`
comments ignored) and drops exact matches. The build copies this dir to
`dist/hallucinations` so it ships with the package.
