# golden — transcription-language.v1 vectors

`<Shape>.<case>.json` conforms to `#/$defs/<Shape>` and to the membership rule. `Refused.<case>.json`
is `{shape, value, reason}`: `value` must be refused as `shape`. Checked by `../validate.mjs`, and read
by meeting-api's and admin-api's tests, so every runtime agrees on the same cases.
