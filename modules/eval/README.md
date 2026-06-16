# @vexa/eval — fill a meeting with speaking bots to debug the stack, hot

The inner loop for working on transcription. Instead of waiting for a real
meeting with real people, you tell the agent *"run a 4-speaker meeting with heavy
overlap"* and seconds later there are bots talking known scripts into a live
meeting while Vexa transcribes it. You watch it in the extension, spot what's
wrong, fix a brick, the **desktop** stack hot-reloads, and you re-run — tight
iteration on the thing you actually care about: does it transcribe and attribute
correctly?

Two properties make it more than a demo:

- **Ground truth.** Every bot speaks a known TTS clip that leads with a self-ID
  ("Boris here…"), so we know *exactly* who said what and when. The captured
  transcript can be **scored automatically** (completeness / leakage /
  attribution), not just eyeballed.
- **Reproducible & controllable.** You dial the stressors — number of speakers,
  speech length, overlap — and replay the *same* meeting shape. Precisely the
  thing you can't do with real humans.

It is **not** desktop-specific. The bots are launched through a Vexa **service
API**, so the same harness drives:

- the **desktop** hot rig (the usual case — fastest iteration), or
- **any Vexa deployment** you point it at (staging, prod) that can launch bots.

And the *same* live workflow works on a genuine meeting with real people on the
desktop — this harness just makes the setup **reproducible and controllable**
instead of ad-hoc.

## What it does — four stages

1. **corpus** — generate & cache a pool of TTS clips per speaker (Deepgram Aura,
   1–30 s). The clip **text is the ground truth**.
2. **launch** — send N speaker bots into the meeting via the service API, **one at
   a time, staggered**, so the egress IP isn't flagged. Each is its own test account.
3. **drive** — the admitted bots speak the clips on a controlled timeline (you set
   #speakers, length, overlap); who-spoke-when is written to `truth.jsonl`.
4. **judge** — pull the captured transcript and score it against ground truth
   (eyeball the same meeting live in the extension at the same time).

## Operating it

The setup, the run commands, the dials, and the metrics are in the agent guide —
**[CLAUDE.md](CLAUDE.md)**. In short: fill `secrets.env`, then
`./bin/eval.sh launch` → `drive` → `judge`.

> Fixtures (`cache/`), `truth.jsonl`, and `secrets.env` are git-ignored — speech
> fixtures and real transcripts **never** go in the repo.
