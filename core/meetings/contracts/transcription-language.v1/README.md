# transcription-language.v1 — the language a meeting bot transcribes in

A meeting bot sends each few seconds of speech to the STT backend. This contract fixes which language
it asks for, who may set it, and when.

## The setting

`LanguageSetting` is `{language, allowed_languages}`, codes `^[a-z]{2,3}$` (Whisper's):

| Mode | Fields | What the bot does per window |
|---|---|---|
| auto | neither | the backend detects the language |
| forced | `language` | pinned to `language` |
| restricted | `allowed_languages` (≥2) | detected freely; a window detected outside the list is transcribed again pinned to `language` (always a member of the list) or, when it is null, to the first entry |
| restricted (one entry) | `allowed_languages` (1) | pinned to that entry |

The one rule the schema cannot state: **when both are set, `language` is in the list.**

## The tiers and their precedence

The first tier that sets either field supplies the whole setting:

1. **meeting** — the `POST /bots` body (`SpawnFields`; `"auto"` sets this tier to auto), and every
   accepted live change (`ConfigUpdate`);
2. **user** — the person's default, `PUT /user/transcription` (`UserPreferenceUpdate`), stored by identity
   and handed to meeting-api in the internal bot-context answer as `transcription_language`;
3. **history** — with neither of the above, the languages the person's recent completed meetings
   were spoken in (each at least 5% of their last 10 meetings' segments, 2 to 3 of them, from at least
   50 segments), as a restricted list with no pinned language;
4. **deployment** — meeting-api's `DEFAULT_TRANSCRIPTION_LANGUAGE` + `DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES`
   (`DeploymentDefault`; Helm `meetingApi.transcriptionLanguage` / `transcriptionAllowedLanguages`);
5. **auto**.

meeting-api resolves it once per spawn, on every spawn path (API, terminal, MCP, calendar auto-join,
invite-by-email), stores the result on the meeting as `data.transcription_language`
(`EffectiveSetting`, with its `source`) and puts it in the bot's invocation as invocation.v1
`language` + `allowedLanguages`.

## The live change

`PUT /bots/{platform}/{native_meeting_id}/config` (`ConfigUpdate` → `ConfigAccepted`) replaces the
running setting. meeting-api publishes it as an acts.v1 `reconfigure` (`language`, `allowedLanguages`)
on `bot_commands:meeting:{meeting_id}`, and stores it (source `meeting`) only when a bot received it;
the bot applies it from its next STT call. Allowed for the meeting's owner and for an owner or
contributor of the workspace it is bound to. Refusals are in `x-routes`.

## The languages a caller can choose from

`GET /transcription/languages` (`LanguageCatalog`) answers which codes a bot this caller sends can
transcribe, and the deployment default:

```json
{ "languages": [{"code": "en", "name": "English"}, {"code": "zh", "name": "Chinese"}, …],
  "default": {"language": null, "allowed_languages": []},
  "model": "large-v3-turbo", "source": "provider" }
```

The list belongs to the model. meeting-api finds the caller's backend as a spawn would (their Settings
backend, else the deployment's), asks a backend the deployment owns for its model (`GET {url}/health`
→ `model`; never a customer's endpoint), and reads that model's codes from the sealed `catalog.json`.
`source` says which model id chose the list: `provider` (the backend named it), `configured` (the
configured model id), `assumed` (none known or not listed → every code the language fields accept).
`default` is the deployment tier, the lowest one: it never narrows `languages`.

`catalog.json` holds Whisper's 100 codes with English names and the model-id → list table. Its
reading is pinned by the `ModelLookup.*` goldens; a `LanguageCatalog` golden lists exactly what its
model reads to.

## Readers

| Reader | Uses |
|---|---|
| meeting-api `bot_spawn/transcription_language.py` | validates every tier, resolves, stores, publishes |
| meeting-api `bot_spawn/language_catalog.py` | `catalog.json`; answers `GET /transcription/languages` |
| identity admin-api | validates and stores the user tier |
| meeting bot `src/transcription-language.ts` | applies the setting and every `reconfigure` |
| terminal, MCP `request_meeting_bot` / `update_bot_config` | write the meeting tier |

Run `node validate.mjs --check`. Changing a shape is a `lane:contract` change: re-seal with
`pnpm seal:contracts`.
