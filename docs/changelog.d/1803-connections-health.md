- **Connections tell an outage from a lost authorization (#1803).** The credential broker's `/ready`
  now checks that its store answers the broker's own credential, so an expired or revoked OpenBao
  token makes it unready (`503`, `reason: store_unavailable`) instead of passing while every Gmail
  and Calendar call fails. Every failed account call carries a `reason` (`reconnect_required`,
  `store_unavailable`, `broker_unreachable` or `provider_error`) and what to do. The agent asks you
  to reconnect only for `reconnect_required`; during an outage it says the account is still
  connected, and the Connections panel does not open while the store is down. Keeping an OpenBao
  token alive stays the operator's job. See [Connections](/connections#when-something-fails).
