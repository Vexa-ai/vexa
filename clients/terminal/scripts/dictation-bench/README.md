# dictation-bench

Measures the composer mic against a real STT: one speaker reads 8 sentences (synthetic TTS) in real
time; the bench reports seconds from speech start to the first word in the box, and from speech end
to the final text, plus requests and bytes uploaded. `old` replays the previous composer loop, `new`
runs `src/ui-kit/dictationEngine`.

    STT_URL=… STT_TOKEN=… npx tsx scripts/dictation-bench/bench.ts new <fixtureDir>

The fixture is the guarantee harness's (`core/meetings/services/bot/eval/guarantee.ts`): a
`fixture.json` with utterances and their f32le PCM files. Never use real recordings.
