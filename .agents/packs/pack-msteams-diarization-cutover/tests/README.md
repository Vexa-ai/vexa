# Tests — pack-msteams-diarization-cutover (#394)

## Unit tests

### `teams-attributor.test.ts`

Location:
`services/vexa-bot/core/src/services/teams-attributor.test.ts`.

22 assertions across 4 test cases. All pass under
`npx tsx services/vexa-bot/core/src/services/teams-attributor.test.ts`.

Coverage:

1. **window-match path** — caption-correlation within `matchToleranceMs`
   of the commit window resolves to the correct speaker name with
   `source: 'window-match'`.
2. **cluster-vote path** — repeated commits on the same cluster_id
   build a majority caption vote; new commits resolve via majority
   even outside the live window.
3. **provisional-cluster-id path** — when neither window nor vote
   resolves, the commit returns `speakerName === clusterId` and
   `source: 'provisional-cluster-id'`; `onLateResolve` fires later
   when enough caption evidence accrues.
4. **reset** — `recordCaption` + `resolve` state cleared on session
   teardown.

Run:

```bash
cd services/vexa-bot/core
npx tsx src/services/teams-attributor.test.ts
```

## Synthetic eval

See `../synthetic/synthetic-gate.md` and the three `*.txt` log
artifacts. Coverage: 9 corpora, 2–5 speakers, overlap, interruption,
panel; boundary recall, segment purity, collab accuracy, transcript
WER (when token is available), pyannote vs wespeaker A/B.

## Integration tests

- **Compose lane:** `../compose/meeting-deployment-test.md` (partial —
  operator-gated).
- **Lite lane:** `../lite/meeting-deployment-test.md` (partial —
  operator-gated).

## Tests **not** added by this pack

- No `tests3/` changes (per skill contract).
- No upstream `tests/` regression suite changes — diarization is a
  new bot subsystem, not a refactor of an existing API surface.
- No Playwright e2e tests for the dashboard transcript surface — out
  of pack scope.
