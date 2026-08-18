- **Microsoft Teams speaker attribution (#499).** Teams uses CSRC transport activity to route the
  mixed audio into per-speaker transcription windows with the same LocalAgreement buffering contract
  as Google Meet. When two speakers' windows produce the same words over shared audio, both rows
  keep those words and the shared run is bracketed — `[like really good]` — on each row, so a
  contested passage is visible instead of being assigned to the wrong person. No winner is inferred.
