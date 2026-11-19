# [key]

The dynamic segment — `key` ∈ {models, transcription, signin} (admin-api validates; unknown keys 404). GET returns the stored value; PUT partial-updates it (empty string clears a field).

`signin` is the admin-edited half of the sign-in allow-list (Vexa-ai/vexa#1783): `{allow}` — exact addresses and `@domain` entries. Its GET also carries the deployment's `VEXA_SIGNIN_ALLOW` half read-only (`env`, `env_problems`); its PUT is all-or-nothing and answers `422` naming every entry it could not read.
