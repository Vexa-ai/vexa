- **An exhausted STT token is now caught where you set it — by transcribing real audio, not by
  guessing from balances or account names.** The config preflight's STT probe posts a ~1s WAV
  (the same request a bot's first chunk makes); a metered backend that answers 402 becomes a
  typed `exhausted` configuration fault that demotes `/health`, refuses bot spawns, and names
  the consequence ("meetings will complete with no transcript"). The old empty-body probe
  could never elicit the 402 and greened the dead token. The wizard's Test button now runs the
  same round-trip and no longer consults `balance_minutes` — which reads 0.0 both for an
  exhausted token and for a billing-exempt service account that transcribes perfectly — and no
  account identity is hardcoded anywhere. The probe is metered, so it caches for 15 minutes.
