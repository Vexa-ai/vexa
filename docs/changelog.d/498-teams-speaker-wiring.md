- **Teams bot speaker attribution: segments carry who spoke, not `seg_N` (#498).** The bot now
  bundles the Teams voice-level-outline speaker watcher (`@vexa/teams-capture` — the same module
  the desktop extension runs) into its browser bundle and wires it to the transcriber: hints cross
  the page→Node boundary under Teams' true `dom-outline` kind (previously erased to Zoom's
  `dom-active`), on one epoch-ms clock with a loud skew guard, and a periodic
  `hint-counters` log line names the exact hop if a name ever goes missing again. A headless
  boundary CI test pins the wiring so the regression cannot ship silently.
