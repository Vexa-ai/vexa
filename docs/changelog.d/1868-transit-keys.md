- **Transit keys (#1868, ADR-0051).** A service that acts on your key, such as the hosted
  transcription API, now asks identity whom a key belongs to and gets a signed answer, instead of
  reading the key table itself. Identity alone mints these keys. Each names the one service it
  opens and the actions it allows, and is listed with your other keys. Existing transcription keys
  keep working unchanged.
