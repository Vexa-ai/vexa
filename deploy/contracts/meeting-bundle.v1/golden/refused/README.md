# golden/refused — must be refused

Each zip breaks exactly one rule; [`refused.json`](refused.json) names the `Refusal` code every
importer must answer it with, and why. They include path traversal (`zip-slip.zip`,
`absolute-path.zip`, `backslash-path.zip`), a symlink, a tampered part, an unknown major version, an
unlisted entry, a decompression bomb, a non-zip, a missing manifest, an executable posing as audio,
and a part carrying deployment-bound fields (a user id and a storage path).
