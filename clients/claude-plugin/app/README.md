# meeting-assistant: the default Vexa app for Claude

An app is a folder, and in a team a Vexa workspace (a git repo): a manifest plus the files it names.
The plugin ships this one. On first run it is copied to `~/.vexa/apps/meeting-assistant/`, the
person's own copy: edit it there. A plugin update merges into that copy: a file never edited takes the
new version, an edited one stays and the new version lands beside it as `<file>.upstream`
(`.upstream/` holds the version last merged). `/vexa-app-reset` puts it back to the plugin's version
and keeps a dated backup. A project's own `.vexa/app/` wins over both (`/vexa-app-seed` copies the
person's app there, to commit and share with the team).

- `vexa-app.json`: the manifest (`schema` `vexa-app/v1`, `name`, `version`, `description`,
  `upstream` {`repo`, `path`, `ref`} it was forked from, `entry` {`pane`, `listener`, `skills`, `flows`}).
- `pane.json`: the pane's copy and switches (`title`, `emptyHint`, `demo`, `offerListening`).
- `listener/<preset>.json`: one listen brief per kind of conversation, in the signals.v1 `Brief`
  shape the app's meeting listener uses (`labels` [{`name`, `question`, `wake`}], `focus`, `known`,
  `wake_at`, `max_wait_s`, `rules` [{`kind`: keyword|person|regex, `value`, `wake`}]). Here `focus`
  holds the plain-language rules the listener's small model reads; `rules` are instant triggers.
  `listener.defaultPreset` in the manifest picks the one used when listening starts.
- `listener/calibration/fixture.json`: labelled lines (`speaker`, `text`, `wake`) that
  `listener_calibrate` replays through the listener's filter to report precision, recall and latency.
- `skills/`, `flows/`: reserved entry points; nothing reads them yet.
