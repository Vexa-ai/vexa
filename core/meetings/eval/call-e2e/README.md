# call-e2e — a call, end to end, through a real LiveKit room

_meetings eval · on a Docker host (bbb), never the laptop · #1783, ADR-0048 decision 6._

`run.sh` starts a throwaway Valkey and **LiveKit server** (the pinned image, host network, its keys
in the environment), the branch's **meeting-api** (real app, real Redis, `calls` from `LIVEKIT_*`, an
in-memory meeting store) and **gateway** as local processes, and uses a real transcription service.
`client.py` starts a call the way Minutes does — `POST /bots {"platform":"vexa_call"}` through the
gateway — then gives two headless participants room tokens with the claims agent-api's token route
mints, and `participants.mjs` (@livekit/rtc-node) joins each one and publishes its own speech WAV as
a microphone track, the second starting after the first has finished. Both leave; the listener
(`LocalBotRuntime`: the branch's `src/index.ts` under `tsx`) ends the call when the room has been
empty for `CALL_EMPTY_GRACE_SEC` and completes the meeting. The client reads the transcript from
`transcription_segments` and prints, per speaker: seconds published, seconds the listener received,
seconds transcribed, and the text.

The speech is synthetic (`speech-a.txt`, `speech-b.txt` through `espeak-ng`, two voices).

Prerequisites on the host: `uv`, Node 22 + `pnpm install --filter @vexa/bot...` and a build of the
bot's workspace dependencies in this checkout, `espeak-ng`, `ffmpeg`, Docker.
