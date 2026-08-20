- **Microsoft Teams speaker attribution (#499).** Teams uses CSRC transport activity to route the
  mixed audio into per-speaker transcription windows with the same LocalAgreement buffering contract
  as Google Meet. Transport-contested words remain explicitly marked instead of being assigned to
  the wrong person.
