- **The `/ws` guard now runs on fastapi-guard, sharing the HTTP guard's ban store (#1324, by
  [Renzo Franceschini](https://github.com/rennf93)).** With `GUARD_WS_ENABLED=true`, a ban from
  either path applies to both, across replicas when `GUARD_ENABLE_REDIS=true`. New knobs:
  `GUARD_AUTO_BAN_RATE_LIMIT` (off by default), `GUARD_IP_WHITELIST` (an allow-list) and
  `GUARD_IP_BLACKLIST`, validated at boot. **Breaking:** `GUARD_WS_ENABLED=true` with
  `GUARD_ENABLED=false` now refuses to start; set `GUARD_ENABLED=true` or turn the WS guard off.
  See [Configuration](/configuration).
