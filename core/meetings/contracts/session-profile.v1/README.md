# session-profile.v1 — what a stored browser session may hold, and its write-back

An authenticated bot joins signed in because it restores a browser session the operator provisioned
(`make login`). This contract bounds that session and the one wire that changes it.

## The profile

`#/$defs/SessionProfile` is the profile itself, as a `const` in the sealed schema: the auth-essential
subset of a Chromium profile (cookies, login data, preferences, web data, and the data files of the
Local Storage and Session Storage LevelDB dirs), about 200 KB. A path is in it when it is listed in
`files`, or when its parent is exactly one of `leveldbDirs` and its whole name matches `leveldbFile`.
Nothing else is, and the limits are part of it: 8 MiB a file, 32 MiB and 128 files together.

| Reader | Uses it for |
|---|---|
| `@vexa/remote-browser` (`SESSION_PROFILE`, `isSessionProfilePath`) | the bot's restore: it lists the stored prefix and downloads only profile paths · the write-back body it collects · the operator's upload and the local save/load |
| meeting-api `session_profile` | the write-back route: it stores only profile paths, within the limits |

**Both runtimes read this schema file verbatim.** Each carries a byte-identical copy
(`core/meetings/modules/remote-browser/src/session-profile.v1.schema.json`,
`core/meetings/services/meeting-api/src/meeting_api/session_profile/session-profile.v1.schema.json`)
and reads `$defs.SessionProfile.const` from it; `gate:fact-parity` fact `session-profile-contract`
fails when either copy differs from this file. To change the profile: edit it here, re-seal
(`pnpm seal:contracts`, a `lane:contract` change), and copy this file over both.

## The write-back

`x-routes`: `PUT /internal/browser-session/{session_uid}` on meeting-api, `WritebackBody` in,
`WritebackResult` out. On clean teardown an authenticated bot sends its rotated session here instead of
writing the store, whose key it holds read-only.

- **The URL** is the one meeting-api puts in the bot's invocation as `sessionWritebackUrl`
  (invocation.v1), session uid included. meeting-api sends it only in authenticated mode; a bot whose
  invocation carries none writes nothing back. The bot derives nothing from other URLs.
- **The credential** is the bot's MeetingToken (invocation.v1 `token`), `Authorization: Bearer`,
  admitted for exactly `{session_uid}`. meeting-api also requires authenticated mode and that the
  session is the live authenticated bot: the newest session spawned on the deployment's identity, its
  meeting live or ended under 600 s.
- **The body** is exactly `{"files": [{"path", "data"}]}`: every path a profile path named once, every
  file standard padded base64, each decoded file within `maxFileBytes` and all of them within
  `maxTotalBytes`. One bad entry refuses the whole body (422) and nothing is written. A body over
  the base64 of `maxTotalBytes` plus framing is refused before it is parsed (413).

**Version note.** `sessionWritebackUrl` is new in invocation.v1 at v0.13.2, and that schema refuses
unknown fields, so a bot older than v0.13.2 refuses an authenticated invocation carrying it. Upgrade
bots and meeting-api together, as the v0.13.2 MeetingToken already requires.

## Goldens

[`golden/`](golden/) — accepted and refused bodies, the route's answer, and the path vectors both
runtime matchers are tested against. `node validate.mjs --check` (`gate:schema`) checks them, that the
profile is a `Profile` whose limits are the ones `WritebackBody` states, and that the route's shapes
exist.
