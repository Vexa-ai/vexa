### Added

**The lane reports what LocalAgreement costs.** A turn re-sends its whole unconfirmed span on every
pass, so the same audio is transcribed once per pass until it confirms. `resendRatio` (audio sent ÷
distinct audio covered) and `maxSubmitSec` (the longest single submission) now ride in every corpus
entry, because that tail is what a user feels as latency.
