# Lite Human Eyeball Gate

Status: pending hot validation

PACK 1 requires hot human-in-the-loop validation against the Lite target before the PR can leave draft. Prior machine checks remain useful supporting evidence, but they do not satisfy the human gate by themselves:

- `lite/isolated-start/`: isolated Lite container started on PACK 1 ports and served gateway/dashboard surfaces.
- `lite/local-storage-playback-smoke-rerun/`: generated a local-storage finalized master, verified the Lite gateway master route returned `200`, and verified raw range playback returned `206`.

Required next evidence:

- Live meeting case URL and target Lite dashboard URL.
- Human confirmation of bot presence, audible speech where applicable, visible transcript/speaker state, and visible recording/playback artifact state.
- Machine transcript/webhook/playback telemetry correlated to the same run.
- Generated Lite API tokens must remain redacted from evidence.
