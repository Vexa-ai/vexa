# meetings/modules — the bricks

The meetings-domain bricks: one concern each, behind a contract, composed by
`meetings/services/`. A brick imports **contracts** and other bricks' **published
packages** (`@vexa/*`, never their `src/`) and third-party deps — never `services/`,
never another brick's internals (`gate:isolation`, P2). Services import bricks.

The transcript spine, in dependency order:

| Brick | Concern | In → out |
|---|---|---|
| [capture-codec](capture-codec/) | the shared capture/recording wire codec (pure) | — |
| _buffer_ | LocalAgreement confirmation core | _(soon)_ |
| _whisper_ | `stt.v1` egress (PCM → Whisper segments) | _(soon)_ |
| _gmeet-capture_ | Google Meet per-channel audio + glow name | page → `gmeet-capture.v1` |
| _gmeet-pipeline_ | channel-routed transcription | `gmeet-capture.v1` → `transcript.v1` |

_Filled in brick-by-brick as each lands gate-green (Stage 3)._
