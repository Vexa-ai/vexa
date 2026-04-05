# Agent Test: API Gateway

## Prerequisites
- Services running: api-gateway, admin-api, postgres, redis (Docker)
- Environment: .env configured with API_GATEWAY_HOST_PORT
- Setup: `docker compose up -d api-gateway admin-api postgres redis`

## Tests

### Test 1: Route Mapping Verification
**Goal:** Verify all API routes are correctly proxied to their backend services.
**Setup:** Fetch the OpenAPI spec from `http://localhost:8056/openapi.json` (or `/docs`).
**Verify:** Each documented route returns a response from the correct backend service (not a 502/503).
**Evidence:** Capture route list and response status for each endpoint.
**Pass criteria:** All routes return responses from expected backends. No 502 (Bad Gateway) errors for running services.

### Test 2: Token Scope Enforcement
**Goal:** Verify that API tokens are validated with correct scope prefixes (vxa_bot_, vxa_tx_, vxa_user_).
**Setup:** Create tokens with different prefixes via admin API. Attempt to access endpoints with tokens of incorrect scope.
**Verify:** Requests with correct scope succeed. Requests with wrong scope are rejected with 403.
**Evidence:** Capture a matrix of token-scope vs endpoint access results.
**Pass criteria:** 100% correct enforcement. No endpoint accessible with an incorrect token scope.

### Test 3: Rate Limiting and Error Handling
**Goal:** Verify the gateway handles backend failures gracefully.
**Setup:** Stop a backend service (e.g., admin-api). Send requests to its routes through the gateway.
**Verify:** Gateway returns 503 Service Unavailable (not a connection error or hang). Response includes a meaningful error message.
**Evidence:** Capture error responses when backend is down vs when it's up.
**Pass criteria:** Consistent 503 responses for unavailable backends. No connection timeouts or hangs longer than 10 seconds.

### Test 4: Authentication Bypass Check
**Goal:** Verify no endpoints are accessible without authentication (except explicitly public ones).
**Setup:** Send requests to all endpoints without an Authorization header.
**Verify:** All protected endpoints return 401. Only explicitly public endpoints (health, docs) are accessible.
**Evidence:** Capture a list of all endpoints and their unauthenticated response codes.
**Pass criteria:** Zero protected endpoints accessible without authentication.
