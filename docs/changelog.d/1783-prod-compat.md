- **Upgrading from 0.12 is invisible to existing callers (#1783).** `POST /bots` reads 0.12 `language`
  values (`en-US`, `EN`, `English`) as the code they name. `PUT /user/webhook` returns the 0.12 account
  record plus the webhook config. See [Upgrading from 0.12](/upgrade-from-0-12).
- **0.12 bot Pods are re-adopted on upgrade (#1783).** The 0.13 runtime re-adopts bot Pods a 0.12
  runtime spawned (`RUNTIME_K8S_ADOPT_UNLABELLED`), so a live meeting is not failed as untracked.
  See [Upgrading from 0.12](/upgrade-from-0-12#upgrade-every-component-together).
- **Two 0.13 behaviours are switches, off on upgrade (#1783).** `VEXA_MEETING_DELETE_LIVE=stop` lets a
  delete stop a live bot and delete at the end (default `refuse`: the 0.12 409).
  `VEXA_LANGUAGE_HISTORY_TIER=on` gives a person who set no language the languages of their recent
  meetings (default off). See [Upgrading from 0.12](/upgrade-from-0-12).
