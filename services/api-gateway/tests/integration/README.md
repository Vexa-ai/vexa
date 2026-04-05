# Integration Tests — api-gateway

## What this tests
- Gateway proxies requests to meeting-api correctly (bot lifecycle endpoints)
- Gateway proxies requests to admin-api correctly (user/org/billing endpoints)
- Gateway proxies requests to transcription-collector correctly (transcript endpoints)
- WebSocket upgrade passes through to real-time transcript delivery
- Auth middleware rejects invalid tokens before proxying
- Rate limiting and CORS headers are applied correctly

## Dependencies
- api-gateway running
- meeting-api, admin-api, transcription-collector reachable (real or mock)
- Postgres (for auth token validation)

## How to invoke
Start a testing agent in this directory. It reads this README and the parent service README to understand what to verify.
