# 0.10 API backward-compat suite (release DoD 6)

Behavioral validation that a 0.10-style client's real flows — REST via the
gateway with `X-API-Key`, admin lifecycle, webhooks config, and the
transcript/status WebSockets — work against a running 0.12 stack.
MOCK_BOT scenarios only. Excluded by owner ruling: interactive-bots
endpoints (API refactor planned) and documented not-wired routes
(`PUT .../config`, `POST .../speak`, transcript share links).

## Running

Opt-in — the default `gate:compose` collection is unchanged without the flag:

```bash
V010_COMPAT=1 make -C deploy/compose stack-test-v010
```

CI runs this as the `validate-v010-compat` leg of `release-images.yml`
(required by `promote`), against the published images.

## V010-BREAK inventory

Where 0.12 intentionally diverges from 0.10, the asserting test is kept and
marked `xfail(strict=True)` with a `V010-BREAK:` reason — so a fix flips it
loudly. Current inventory (see the PR/release notes for the ruling):

1. `GET /bots/status` envelope: `{"running_bots":[...]}` → `{"running":[...],"count":N}`
2. `DELETE /bots/{platform}/{id}`: `202 + {"message":…}` → `200 + {"status":"stopping",…}` (stop intent still honored)
3. Admin API no longer reachable via the 0.10 gateway forwarding path (direct admin-api works)
4. `PUT /user/webhook` echoes a reduced user shape (config still applies)
5. `/ws` user-channel auto-subscribe emits unsolicited flat `meeting.status` frames (no sealed payload envelope)
6. Admin user/token mint responses dropped `created_at` (0.10-required field)

Side finding: webhook signature recipe changed to `hmac(ts.payload)`
(0.10: `hmac(payload)`).
