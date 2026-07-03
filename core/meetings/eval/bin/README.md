# meetings/eval/bin

[`eval.sh`](eval.sh) — the harness entrypoint. Sources `secrets.env` (or `$SECRETS`),
then runs a stage: `launch` (send bots in) · `drive` (speak the timeline + log truth) ·
`judge` (score the transcript) · `corpus` (regenerate TTS clip pools, rare). All knobs
are env — see [`../README.md`](../README.md).
