# Agent Test: Admin API

## Prerequisites
- Services running: admin-api, postgres (Docker)
- Environment: ADMIN_API_TOKEN set in .env
- Setup: `docker compose up -d admin-api postgres`

## Tests

### Test 1: API Contract Verification
**Goal:** Verify all documented API endpoints exist and accept the expected parameters.
**Setup:** Fetch the OpenAPI spec from `http://localhost:8057/openapi.json`.
**Verify:** All endpoints listed in the spec are reachable. Request/response schemas match documentation.
**Evidence:** Capture the OpenAPI spec. Test each endpoint with a valid request and record status codes.
**Pass criteria:** All documented endpoints return 200/201 for valid requests. No undocumented 500 errors.

### Test 2: CRUD Operations
**Goal:** Verify create, read, update, delete operations work correctly for all managed resources.
**Setup:** Create a test user via the admin API. Then perform read, update (PATCH with JSONB merge), and delete.
**Verify:** Each operation returns the expected response. Data persists correctly between operations.
**Evidence:** Capture request/response pairs for each CRUD operation.
**Pass criteria:** Create returns 201. Read returns the created data. Update merges JSONB correctly (does not overwrite unrelated fields). Delete removes the resource.

### Test 3: Token Management
**Goal:** Verify API token creation, listing, and revocation work correctly.
**Setup:** Create a new API token via admin endpoint. Use it to make an authenticated request. Revoke it. Try the request again.
**Verify:** Token works before revocation, fails after revocation.
**Evidence:** Capture the authentication results before and after revocation.
**Pass criteria:** Valid token returns 200. Revoked token returns 401. Token list accurately reflects active tokens.

### Test 4: Input Validation
**Goal:** Verify the API rejects malformed inputs with appropriate error messages.
**Setup:** Send requests with: (a) missing required fields, (b) wrong types, (c) excessively long strings, (d) SQL injection attempts in string fields.
**Verify:** All invalid inputs return 400/422 with descriptive validation errors. No 500 errors.
**Evidence:** Capture error responses for each invalid input case.
**Pass criteria:** All malformed inputs rejected with appropriate status codes. No unhandled exceptions in server logs.
