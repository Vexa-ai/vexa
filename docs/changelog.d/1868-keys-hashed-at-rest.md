- **API keys are stored as hashes (#1868).** The database now holds only each key's SHA-256 and a
  hint (`vxa_bot_…AbCd`); the key itself is shown once, when it is created. Every existing key is
  hashed in place on upgrade and keeps working. The upgrade is one-way: take a database backup
  before it. See [Identity](/core/identity).
