- **Upgrading from 0.12 is invisible to existing callers (#1783).** `POST /bots` reads 0.12 `language`
  values (`en-US`, `EN`, `English`) as the code they name. `/ws` keeps the 0.12 key contract by default
  (`VEXA_WS_SCOPE_MODE=strict` applies the REST scopes). `PUT /user/webhook` returns the 0.12 account
  record plus the webhook config. Webhook URLs saved before the upgrade keep their 0.12 delivery
  (`VEXA_WEBHOOK_STORED_URL_POLICY`, default `v012`); new URLs are checked against the 0.13 rules when
  saved. See [Upgrading from 0.12](/upgrade-from-0-12).
- **A bot in a call during the upgrade keeps its transcript (#1783).** meeting-api admits the unsigned
  entries of meetings a 0.12 bot was already transcribing, for a bounded window
  (`VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S`, default four hours). The 0.13 runtime re-adopts those bot
  Pods (`RUNTIME_K8S_ADOPT_UNLABELLED`). See [Upgrading from 0.12](/upgrade-from-0-12#upgrade-every-component-together).
- **Two 0.13 behaviours are switches, off on upgrade (#1783).** `VEXA_MEETING_DELETE_LIVE=stop` lets a
  delete stop a live bot and delete at the end (default `refuse`: the 0.12 409).
  `VEXA_LANGUAGE_HISTORY_TIER=on` gives a person who set no language the languages of their recent
  meetings (default off). See [Upgrading from 0.12](/upgrade-from-0-12).
