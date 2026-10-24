# meeting-assistant: the default Vexa app for Claude

An app is a folder, and in a team a Vexa workspace (a git repo): a manifest plus the files it names.
The plugin ships this one and reads it unless the project has its own copy at `.vexa/app/`
(`/vexa-app-seed` copies this folder there to start one; edit it, commit it, share it).

- `vexa-app.json`: the manifest (`schema` `vexa-app/v1`, `name`, `version`, `description`,
  `upstream` {`repo`, `path`, `ref`} it was forked from, `entry` {`pane`, `listener`, `skills`, `flows`}).
- `pane.json`: the pane's copy and switches (`title`, `emptyHint`, `demo`, `offerListening`).
- `listener/<preset>.json`: one listen brief per kind of conversation, in the signals.v1 `Brief`
  shape the app's meeting listener uses (`labels` [{`name`, `question`, `wake`}], `focus`, `known`,
  `wake_at`, `max_wait_s`, `rules` [{`kind`: keyword|person|regex, `value`, `wake`}]).
  `listener.defaultPreset` in the manifest picks the one used when listening starts.
- `skills/`, `flows/`: reserved entry points; nothing reads them yet.
