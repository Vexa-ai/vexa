# Agent Test: Deep Audit

## Prerequisites
- Repository cloned and accessible
- Python 3.8+ available
- Setup: Run deterministic audits first:
  - `python tests/audit/security_audit.py`
  - `python tests/audit/config_audit.py`
  - `python tests/audit/architecture_audit.py`

## Tests

### Test 1: Authentication Flow Review
**Goal:** Verify all authentication flows are secure and complete.
**Setup:** Read the auth-related code in api-gateway and admin-api. Trace the token lifecycle from creation to validation to revocation.
**Verify:** Tokens are generated with sufficient entropy. Token validation checks scope prefix. Revoked tokens are immediately rejected (not cached). No timing attacks on token comparison.
**Evidence:** Document the token lifecycle flow. List all endpoints and their auth requirements.
**Pass criteria:** All endpoints require authentication (except health/docs). Token entropy is at least 128 bits. Revocation is immediate.

### Test 2: Data Flow Analysis
**Goal:** Verify sensitive data is handled correctly throughout the system.
**Setup:** Trace data flow from audio input to transcript storage to API response.
**Verify:** Audio data is not persisted longer than necessary. Transcripts are stored with correct user ownership. API responses do not leak data from other users. Database queries use parameterized statements.
**Evidence:** Document the data flow diagram. List all data persistence points and their retention policy.
**Pass criteria:** No cross-user data leakage. Parameterized queries everywhere. Audio cleaned up after processing.

### Test 3: Error Handling Review
**Goal:** Verify error handling does not leak sensitive information.
**Setup:** Review error handling patterns across all services.
**Verify:** Stack traces are not returned to clients. Error messages do not reveal internal architecture. Failed auth does not distinguish between "user not found" and "wrong password". Database errors are wrapped.
**Evidence:** Capture example error responses from each service. List any cases where internal details are exposed.
**Pass criteria:** Zero stack traces in API responses. Generic error messages for auth failures. All database errors wrapped.

### Test 4: Dependency Audit
**Goal:** Verify third-party dependencies are up to date and free of known vulnerabilities.
**Setup:** Check requirements.txt, package.json, and Dockerfile base images.
**Verify:** No dependencies with known CVEs. Base images are recent. No pinned versions with known issues.
**Evidence:** Run `pip audit` (Python) and `npm audit` (Node.js) where applicable. List any findings.
**Pass criteria:** Zero high/critical CVEs. All dependencies within 1 major version of latest.

### Test 5: Docker Security Review
**Goal:** Verify Docker configuration follows security best practices.
**Setup:** Review all Dockerfiles and docker-compose files.
**Verify:** Containers run as non-root where possible. No unnecessary capabilities. Secrets are not baked into images. Health checks are defined. Resource limits are set.
**Evidence:** List each container's user, capabilities, and resource limits. Note any running as root.
**Pass criteria:** Application containers run as non-root. No hardcoded secrets in Dockerfiles. Health checks defined for all services.
