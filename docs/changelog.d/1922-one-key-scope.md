- **One key does everything its owner may do (#1922).** `bot` and `tx` grant the same routes;
  ownership and workspace decide. A `bot`-only key reads its transcripts and streams them over `/ws`;
  a `tx`-only key sends bots and deletes its own meetings. No key loses access.
