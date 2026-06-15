# webhook.v1 — signed delivery to customer endpoints (to version at MVP4)

HMAC-SHA256 signed JSON with exponential backoff retry — live since v0.9
(`meeting-api/{callbacks,outbound_events,post_meeting}.py`). MVP4 freezes payload
schemas + goldens (fixtures: golden payloads + fake receiver harness).
