# Overall Functionality Gate

Status: blocked after failed Compose human playback observation

Synthetic, Compose, and Lite machine checks demonstrate the PACK 1 playback route behavior, but they do not complete the required hot human-in-the-loop gate. The first live Compose run exposed a dashboard freshness/playback trust failure from the human perspective and the PR must remain draft.

Machine-validated coverage:

- Meeting API synthetic recording/finalizer tests: `14 passed`.
- API Gateway recording route tests: `11 passed`.
- Dashboard canonical master API tests: `3 passed`.
- Dashboard recording refresh signature tests: `1 passed`.
- Dashboard production build: passed after syncing local packages; passed again after commit `6aae0fa`.
- Compose playback smoke: master route `200`, raw range `206`.
- Lite playback smoke: master route `200`, raw range `206`.
- Hardenloop: zero normalized release blockers; scanner coverage caveat recorded in `.agents/releases/0.10.6.x-replay/state.md`.

Live/human status:

- Compose run `human/compose-hot/compose-bvf-rzuj-kwj-20260523T155551Z/`: machine transcript/webhook/recording evidence passed, but the human playback/finalization observation failed.
- Fix applied in product commit `6aae0fa`: dashboard refresh now detects playback-readiness changes on existing recordings, not just recording count changes.
- Compose rerun `human/compose-hot/compose-bvf-rzuj-kwj-rerun-20260523T163114Z/`: blocked before speech because the bots remained `awaiting_admission` until timeout; cleanup completed.
- Compose post-fix rerun `human/compose-hot/compose-bvf-rzuj-kwj-postfix-20260523T165318Z/`: invalid/fail. Bots were admitted, but only 8/16 scripted turns were accepted before the bots self-completed; human playback observation failed with the audio control stuck at `Preparing audio...` and `0:00 / 0:00`. Backend/fresh-load evidence later showed the recording master was completed and playable, so the next run must begin from a hard-refreshed patched dashboard client.
- Lite hot validation has not been rerun after the fix.

Required next step: rerun hot validation against both Compose and Lite targets with admitted bots, record human observations in this evidence tree, rerun the evidence checker, and only then consider the PR ready to leave draft.

Post-pass outcome (added after the fact, file otherwise preserved as historical record): the code reviewer signed off on the PR #364 split-view diff on 2026-05-24 (verdict `pass`, see `code-review.md`), the post-pass trust fixes `898b261 fix(dashboard): neutralize "missing recording" red banner` and `60644d2 fix(recordings): self-heal finalizer when bot exits before chunk-write` landed, and epic #356 was promoted to `status:ready-for-stage`. This pack PR was accepted into the 0.10.6.3 stitched candidate.
