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

Post-pass outcome (added after the fact, file otherwise preserved as historical record): the Compose hot gate carried the human signal for this pack (see `../compose/human-eyeball.md` and `../human/overall-functionality.md`). After the code-review pass verdict and the post-pass trust fixes, epic #356 was promoted to `status:ready-for-stage` and this PR was accepted into the 0.10.6.3 stitched candidate. The Lite machine smoke (`lite/local-storage-playback-smoke-rerun/`) remained the supporting Lite signal; a dedicated Lite hot human run was not re-scheduled before release acceptance.

The post-stitch evidence-only fix `5acf36c fix(pack-1): use @example.com (RFC 2606) instead of @example.invalid (RFC 6761)` updated this directory's sibling `../ops/lite_playback_smoke.py` so the stitched 0.10.6.3 candidate's `GET /admin/users` no longer 500'd on Pydantic v2 EmailStr validation.
