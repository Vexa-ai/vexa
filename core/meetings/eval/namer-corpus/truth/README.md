# Ground truth, one file per tape

Produced by [`../truth.py`](../truth.py); consumed by [`../score.py`](../score.py). One file per
tape in the corpus, including the tapes whose tracks could not be truthed — an excluded track
carries its reason here, so an absence from the scorecard is always explained by a file rather
than by a gap.

Each file records the method, the thresholds it was decided under, the per-track purity and
owner-share that cleared them, the roster's first-sighting timeline (the quantity the
roster-settle fix is about), and any linguistic judge verdict.

Names are pseudonymized per tape as `P1`, `P2`, … — in the track truths, in the roster timeline,
and inside the exclusion reason strings. The real-name map is written by `truth.py --map-out` to
an operator-chosen path and never enters the repository.
