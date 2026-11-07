- **admin-api and meeting-api need a database password of their own (#1784).** Neither service
  falls back to a password any more: each refuses to boot when `DB_PASSWORD` is unset or is a value
  published in this repository (`postgres`, `password` and the internal-secret placeholders). Compose,
  the Helm chart and Lite already mint one; **if you run either service yourself, set `DB_PASSWORD`**
  to your database's password. See [Configuration](/configuration#database--storage).
