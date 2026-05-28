# Security Advisory Draft

Status: private draft
Target: `/home/dima/dev/vexa-pack-0.10.6x-pack-1-recording-playback-trust`

## Repository Profile

- Languages: javascript, python, typescript
- Package managers: npm, pip
- API clients: anthropic, fetch, got, httpx, openai, requests, stripe
- Route hints: /, /analytics/meetings, /analytics/meetings/{meeting_id}/telematics, /analytics/users, /analytics/users/{user_id}/details, /api/chat, /api/chat/reset, /api/schedule, /api/sessions, /api/sessions/{session_id}, /api/workspace/file, /api/workspace/files, /api/workspaces, /api/workspaces/{name}, /api/workspaces/{name}/file, /api/workspaces/{name}/files, /auth/me, /b/{token}, /b/{token}/save, /b/{token}/storage

## Findings

### Possible hardcoded secret

- ID: `secret-5e7cedea3c`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/auth-validate3.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-caa1f3f254`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/agent-flow.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-41c059f72c`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/feature-validate.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-522412739f`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/auth-validate-final.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-8118fd3d54`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/auth-validate.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-fb8386fcdd`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/agent-inspect.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-761ccce9e9`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/auth-validate2.js:3` `const TOKEN = 'vxa_user_jIwBRUBlQcLeV0aCuYXOtvzNnlC28wpttcPxOXET';`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-b6aef6d635`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/src/components/mcp/mcp-config-button.tsx:82` `VEXA_API_KEY: "YOUR_API_KEY_HERE",`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-c0cc5f9ac3`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `services/dashboard/src/app/docs/admin/users/page.tsx:134` `token: "vex_abc123def456...", // Only shown once!`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Possible hardcoded secret

- ID: `secret-ad2aef6a9f`
- Severity: high
- Confidence: medium
- Category: secrets
- Impact: A leaked credential can allow account takeover, data exfiltration, or unauthorized API spend.
- Evidence: `deploy/helm/charts/vexa/values-test.yaml:9` `adminApiToken: "test-admin-token-t3"`
- Remediation: Move credentials to secret storage, rotate exposed values, and add secret scanning to CI.

Validation strategy:
- Objective: Confirm whether the exposed value is live or has release impact without using it against external services.
- Owner: machine
- Expected signal: Credential is rotated or proven to be a non-secret placeholder.

### Outbound API call may be missing a timeout

- ID: `timeout-aaccf3aa70`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/runtime-api/Dockerfile:18` `CMD python -c "import httpx; httpx.get('http://localhost:8090/health').raise_for_status()" || exit 1`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-994819f4b1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/api-gateway/main.py:1690` `const res = await fetch('/b/' + TOKEN + '/save', {{ method: 'POST' }});`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-56e3eac971`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/join/join-modal.tsx:232` `const response = await fetch(withBasePath("/api/vexa/bots"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-0e141d24f1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/meeting-agent-panel.tsx:157` `const resp = await fetch(`${AGENT_API}/chat`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3cd6bc7aa8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/meeting-agent-panel.tsx:238` `await fetch(`${AGENT_API}/chat`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2447bbc0ce`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/meeting-agent-panel.tsx:252` `await fetch(`${AGENT_API}/chat/reset`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-e98b745d5b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/agent-chat.tsx:227` `const resp = await fetch(`${AGENT_API}/chat`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9f62267db5`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/agent-chat.tsx:308` `await fetch(`${AGENT_API}/chat`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d4092747a9`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/agent/agent-chat.tsx:320` `await fetch(`${AGENT_API}/chat/reset`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-09dfd77958`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/notifications/notification-banner.tsx:63` `const resp = await fetch(`${BLOG_URL}/notifications.json`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5ddf21a4c2`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:121` `const resp = await fetch(`${AGENT_API}/workspace/tree?user_id=${userId}`);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9e6d03a0d0`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:131` `const resp = await fetch(`${AGENT_API}/workspace/diff?user_id=${userId}`);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3ff8f68ee0`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:150` `const resp = await fetch(`${AGENT_API}/workspace/file?user_id=${userId}&path=${encodeURIComponent(path)}`);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8c00912af7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:168` `const resp = await fetch(`${AGENT_API}/workspace/file`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d52bafed80`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:187` `const resp = await fetch(`${AGENT_API}/workspace/commit`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-87728c7c68`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/workspace/workspace-editor.tsx:208` `const resp = await fetch(`${AGENT_API}/workspace/file`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-da0dfa796b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/mcp/mcp-config-button.tsx:41` `const response = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2b93031988`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/decisions/decisions-panel.tsx:449` `const res = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-03285ab8bf`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/ai/ai-chat-panel.tsx:89` `const response = await fetch(withBasePath("/api/ai/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-bdb029c07d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:61` `fetch(withBasePath("/api/config"))`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b722bf0c23`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:124` `const response = await fetch(withBasePath(`/b/${token}/save`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-4cad254c4a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:140` `const response = await fetch(withBasePath(`/b/${token}/storage`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b9adf08f43`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:155` `const response = await fetch(withBasePath(`/api/vexa/bots/browser_session/${meeting.platform_specific_id}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-efa97c2210`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/admin/admin-guard.tsx:26` `const response = await fetch(withBasePath("/api/auth/admin-verify"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-69d60ac11f`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/layout/sidebar.tsx:62` `fetch(withBasePath("/api/billing/status"))`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c8bde5225c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/transcript/transcript-viewer.tsx:542` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5f98b5918e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/mcp/page.tsx:28` `const response = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-99a6d13c9b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/tracker/page.tsx:140` `const res = await fetch(`${decisionListenerUrl}/config`);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c4b0b19403`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/tracker/page.tsx:162` `const res = await fetch(`${decisionListenerUrl}/config`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-472bdf226e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/tracker/page.tsx:183` `const res = await fetch(`${decisionListenerUrl}/config/reset`, { method: "POST" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-cd9a2fd5f5`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/auth/zoom/callback/page.tsx:54` `const completeResp = await fetch(withBasePath("/api/zoom/oauth/complete"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-71dcac9a1d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/auth/google-calendar/callback/page.tsx:46` `const completeResp = await fetch(withBasePath("/api/calendar/oauth/complete"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f3e672919c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/auth/verify/page.tsx:61` `const response = await fetch(withBasePath("/api/auth/verify"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-95b2964a9e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:88` `const resp = await fetch(`https://zoom.us/oauth/token?${params.toString()}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-14d6409238`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:29` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-53f61e9a32`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:47` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-14a3a856be`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:63` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5e1774e503`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:76` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b1caec9e18`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:89` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8d171330f8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/health/route.ts:107` `const response = await fetch(`${adminApiUrl}/admin/users?limit=1`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-e4da0b4734`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/health/route.ts:158` `const response = await fetch(`${vexaApiUrl}/`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c221b9e58a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:28` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-210159c8b1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:46` `const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b3aeae8577`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:56` `const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c357ebd978`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/route.ts:36` `const meetingsRes = await fetch(`${VEXA_API_URL}/meetings`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d7653d1188`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/route.ts:98` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6857e23475`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/[meetingId]/route.ts:20` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5877c1b9ab`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:32` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-16f8d3d25e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:86` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-7b2e736df2`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:113` `const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-94d26a58e9`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:124` `const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-a189518a4b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:141` `await fetch(`${VEXA_API_URL}/user/webhook`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3d6069d750`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/notifications/route.ts:16` `const response = await fetch(notificationsUrl, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2d4784ba4c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:37` `const botsResp = await fetch(`${VEXA_API_URL}/bots?${qs.toString()}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-1d37bfda33`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:52` `const statusResp = await fetch(`${VEXA_API_URL}/bots/status`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-09c357535f`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:116` `const response = await fetch(url, { ...fetchOptions, cache: "no-store" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-e21a8e4b95`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:89` `const resp = await fetch("https://oauth2.googleapis.com/token", {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c92c5762cf`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/auth/send-magic-link/route.ts:33` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3138dd53cb`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/auth/me/route.ts:25` `const response = await fetch(`${VEXA_API_URL}/auth/me`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6ea1fcc9ca`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/ai/chat/route.ts:53` `return fetch(new Request(requestUrl.toString(), { ...url, headers }));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5ccc3a4098`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/ai/chat/route.ts:55` `return fetch(requestUrl.toString(), { ...options, headers });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-69eecfb3e8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/route.ts:30` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5743f9858d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/route.ts:87` `const response = await fetch(url, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-1f64c69e6f`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/[id]/route.ts:30` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/tokens/${id}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-58652ff849`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/admin/[...path]/route.ts:92` `const response = await fetch(url, fetchOptions);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c6a863b546`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/settings/page.tsx:42` `const configResponse = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3a78a22915`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/settings/page.tsx:51` `const response = await fetch(withBasePath("/api/ai/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-559f52eb58`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-transcripts/page.tsx:80` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-78669fce49`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-transcripts/page.tsx:134` `response = requests.get(url, headers=headers)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-91e7f053b4`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/share-transcript-url/page.tsx:83` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-4eedc18759`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/share-transcript-url/page.tsx:145` `response = requests.post(url, headers=headers, params=params)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-ceabd82598`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/rename-meeting/page.tsx:66` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-4a2833ead8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/rename-meeting/page.tsx:97` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6c8d2d647b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/rename-meeting/page.tsx:148` `response = requests.patch(url, headers=headers, json=payload)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-62a9f194dc`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/rename-meeting/page.tsx:175` `response = requests.patch(url, headers=headers, json=payload)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-7293dcfbad`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-status-history/page.tsx:83` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-15e636f22b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-status-history/page.tsx:122` `const response = await fetch('https://your-api-url/meetings', {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-473266b1d7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-status-history/page.tsx:158` `response = requests.get(url, headers=headers)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-33c0cd1055`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/docs/cookbook/get-status-history/page.tsx:189` `response = requests.get(url, headers=headers)`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6b7339d478`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/page.tsx:118` `const response = await fetch(withBasePath("/api/vexa/bots"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-99b1ce8305`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:1804` `const response = await fetch(withBasePath(`/b/${sessionToken}/save`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-366c83cf37`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:2260` `const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-580821def6`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:2276` `await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, { method: "DELETE" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-50bb464859`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/login/page.tsx:60` `const res = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6a53ae2868`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/login/page.tsx:74` `const response = await fetch(withBasePath("/api/health"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-1c0761594b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:126` `const response = await fetch(withBasePath(`/api/profile/keys?userId=${user.id}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-53b7b1d93f`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:159` `const response = await fetch(withBasePath("/api/profile/keys"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-26da28cbb4`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:196` `const response = await fetch(withBasePath(`/api/profile/keys/${keyId}`), { method: "DELETE" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5a090296e7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:519` `fetch(withBasePath("/api/vexa/user/workspace-git")).then(async (r) => {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c5ef1f814b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:537` `const response = await fetch(withBasePath("/api/vexa/user/workspace-git"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-69db6d3012`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:556` `await fetch(withBasePath("/api/vexa/user/workspace-git"), { method: "DELETE" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-51d33d2d0d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/profile/page.tsx:572` `const response = await fetch(`https://api.github.com/repos/${repoPath}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2d1936b97c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:113` `const response = await fetch(withBasePath(`/api/webhooks/deliveries?${params}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-685a4bfd57`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:136` `const response = await fetch(withBasePath(`/api/webhooks/deliveries/${meetingId}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-4b7035f0a2`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:156` `const response = await fetch(withBasePath(`/api/webhooks/config${params}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-a5b3068ffa`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:175` `const response = await fetch(withBasePath("/api/webhooks/config"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5779159a6d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:191` `const response = await fetch(withBasePath("/api/webhooks/test"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2782f37c08`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/webhook-store.ts:201` `const response = await fetch(withBasePath("/api/webhooks/rotate-secret"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2cc7c85427`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/admin-auth-store.ts:27` `const response = await fetch(withBasePath("/api/auth/admin-verify"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-eb1acabae2`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/admin-auth-store.ts:62` `fetch(withBasePath("/api/auth/admin-logout"), { method: "POST" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-40ce7d0972`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:43` `const response = await fetch(withBasePath("/api/auth/send-magic-link"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-af7ccb3b6b`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:100` `fetch(withBasePath("/api/auth/logout"), { method: "POST" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-85a11d2b97`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:132` `const response = await fetch(withBasePath("/api/auth/me"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-bd00a7ede1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:151` `const oauthResponse = await fetch(withBasePath("/api/auth/oauth-callback"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-74d19cee9a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/agent-store.ts:57` `const resp = await fetch(`${AGENT_API}/sessions?user_id=${userId}`);`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-efb5a22817`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/agent-store.ts:85` `const resp = await fetch(`${AGENT_API}/sessions?user_id=${userId}&name=${encodeURIComponent(name)}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-4ddcea6fff`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/agent-store.ts:111` `await fetch(`${AGENT_API}/sessions/${sessionId}?user_id=${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-968fe83796`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/agent-store.ts:128` `await fetch(`${AGENT_API}/sessions/${sessionId}?user_id=${userId}&name=${encodeURIComponent(name)}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-0cd45e3bde`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/hooks/use-runtime-config.ts:22` `const response = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-de4b97cc16`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/hooks/use-live-transcripts.ts:168` `const configResponse = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2236c5c85c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/hooks/use-live-transcripts.ts:181` `const configResp = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-89cc5e56cb`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/hooks/use-vexa-websocket.ts:41` `const response = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-a5e41e89e8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/vexa-admin-api.ts:137` `const response = await fetch(url, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c63bc3b030`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:42` `const response = await fetch(withBasePath(`/api/admin/users?skip=${skip}&limit=${limit}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3d45cdbfbe`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:49` `const response = await fetch(withBasePath(`/api/admin/users/${userId}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-48e1e3e4f6`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:54` `const response = await fetch(withBasePath(`/api/admin/users/email/${encodeURIComponent(email)}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b8a83f819a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:59` `const response = await fetch(withBasePath("/api/admin/users"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-fb5c47629c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:68` `const response = await fetch(withBasePath(`/api/admin/users/${userId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8bb2a132fd`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:81` `const response = await fetch(withBasePath(`/api/admin/users/${userId}/tokens`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f3a8b018f4`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:89` `const response = await fetch(withBasePath(`/api/admin/tokens/${tokenId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-769da48738`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/admin-api.ts:104` `const response = await fetch(withBasePath("/api/admin/users?limit=1"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-104ff8d05d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:105` `const response = await fetch(withBasePath(`/api/vexa/meetings${qs ? `?${qs}` : ""}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-414f93a678`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:114` `const response = await fetch(withBasePath(`/api/vexa/meetings/${id}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f8de663ff4`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:135` `const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}${params}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9f54cc858e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:217` `const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}/share${qs ? `?${qs}` : ""}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6badd8ce6a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:225` `const response = await fetch(withBasePath("/api/vexa/bots"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-464eef7340`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:235` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-92bd0f8504`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:252` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/config`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-220c182c4c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:274` `const response = await fetch(withBasePath("/api/vexa/bots/status"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f9c0b91fff`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:301` `const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-94cb4594f6`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:311` `const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-68c795c85d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:334` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/chat`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-510115fcd5`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:359` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2940acb206`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:402` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-ffacb2c6a5`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:448` `const response = await fetch(withBasePath(`/api/vexa/meetings/${meetingId}/transcribe`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-ecd8de3b06`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:459` `const response = await fetch(withBasePath("/api/vexa/meetings"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-1731dddb45`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/zoom-oauth-client.ts:84` `const resp = await fetch(withBasePath("/api/zoom/oauth/start"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c76e71ee6e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/auth-utils.ts:27` `const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-6c59b6339c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/auth-utils.ts:47` `const res = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-99515640e1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/docs/code-generator.ts:104` `let js = `const response = await fetch('${url}', {`;`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f083c1e9f8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/vexa-bot/core/src/services/unified-callback.ts:153` `const response = await fetch(baseUrl, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-14dac7f703`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/vexa-bot/core/src/services/transcription-client.ts:198` `const response = await fetch(this.serviceUrl, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Possible secret or token logging

- ID: `secret-log-05f34ccb79`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/dashboard/src/hooks/use-live-transcripts.ts:194` `console.log("[LiveTranscripts] Connecting to:", wsUrl.replace(/api_key=([^&]+)/, "api_key=***"));`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-4407e56bae`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/dashboard/src/hooks/use-vexa-websocket.ts:226` `console.log("WebSocket: Connecting to", wsUrl.replace(/api_key=([^&]+)/, "api_key=***"));`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-7fbc3ad67a`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/vexa-bot/core/entrypoint.sh:91` `SSH_PASS=$(echo "$BOT_CONFIG" | node -e "try{const c=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log(c.session_token||'vexa')}catch{console.log('vexa')}" 2>/dev/null || echo "vexa")`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-7bdeaee83b`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:33` `log('[Zoom Web] recordingUploadUrl or token missing — skipping audio capture');`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-59ad413753`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/vexa-bot/core/src/platforms/msteams/recording.ts:51` `log("[Teams Recording] recordingUploadUrl or token missing — skipping audio capture");`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-7f097ce039`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:44` `log("[Google Recording] recordingUploadUrl or token missing — skipping audio capture");`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-4bdf65d699`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `libs/admin-models/admin_models/token_scope.py:21` `logger = logging.getLogger("admin_models.token_scope")`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-c025c89e3e`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `packages/vexa-cli/vexa_cli/main.py:70` `console.print("[red]No API key.[/] Run [bold]vexa config[/] or set VEXA_API_KEY.")`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible hardcoded password: 'vxa'

- ID: `scanner-db7916bb7c13`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `libs/admin-models/admin_models/token_scope.py:23` `22 
23 TOKEN_PREFIX = "vxa"
24 TOKEN_PATTERN = re.compile(r"^vxa_([a-z]+)_(.+)$")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Probable insecure usage of temp file/directory.

- ID: `scanner-086e7c309535`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `packages/vexa-cli/vexa_cli/repl.py:45` `44     history_path = client.endpoint.replace("://", "_").replace("/", "_").replace(":", "_")
45     history_file = "/tmp/.vexa_history_%s" % history_path
46     prompt_session = PromptSession(history=FileHistory(history_file))
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Standard pseudo-random generators are not suitable for security/cryptographic purposes.

- ID: `scanner-68d510957f2a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `packages/vexa-client/vexa_client/test_funcs.py:19` `18         # Use the new method that automatically sets user_id
19         new_user = admin_client.create_user_and_set_id(email=f"{random.randint(1, 1000000)}@example.com", 
20                                                        name="te`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-258005c36199`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/agent-api/agent_api/container_manager.py:238` `237             await self._http.post(f"/containers/{container}/touch")
238         except Exception:
239             pass
240 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Probable insecure usage of temp file/directory.

- ID: `scanner-fb24bb40bf4a`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/agent-api/agent_api/container_manager.py:308` `307         if info:
308             await self.exec_simple(info.name, ["rm", "-f", "/tmp/.agent-session"])
309 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-5da99123398c`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/agent-api/agent_api/main.py:389` `388         hostname = parsed.hostname or ""
389         if hostname in ("localhost", "127.0.0.1", "0.0.0.0") or hostname.endswith(".internal"):
390             raise HTTPException(400, "Cannot schedule requests to internal URLs")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-447ab4f9036d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:280` `279         await app.state.redis.close()
280     except Exception:
281         pass
282 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-bbf1d19be9e7`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:388` `387                 return json.loads(cached)
388         except Exception:
389             pass  # Redis down — fall through to admin-api
390 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-ad2e46b86188`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:409` `408                     await redis_client.set(cache_key, json.dumps(user_data), ex=60)
409                 except Exception:
410                     pass  # Redis write failure is non-fatal
411             return user_data
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Continue detected.

- ID: `scanner-072c154327ea`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:918` `917             lines.append(f"[{timestamp}] {speaker}: {text}")
918         except Exception:
919             continue
920 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Continue detected.

- ID: `scanner-f5324a95a5dd`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1029` `1028             lines.append(f"[{timestamp}] {speaker}: {text}")
1029         except Exception:
1030             continue
1031 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-f591341411bc`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1188` `1187                     segments = all_segments[-50:]  # latest 50 segments max
1188             except Exception:
1189                 pass
1190 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-6561ef0e3eff`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1583` `1582                             f"browser_session:{token}", updated, ex=86400)
1583                     except Exception:
1584                         pass
1585         except Exception as exc:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-2e8e624288dc`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1800` `1799                             await websocket.send_text(message)
1800                 except Exception:
1801                     pass
1802 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-9d3c497f24c3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1826` `1825             await websocket.close()
1826         except Exception:
1827             pass
1828 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-9a4cfa3b840c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1952` `1951                             await websocket.send_bytes(message)
1952                 except Exception:
1953                     pass
1954 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-f254d4285022`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1977` `1976             await websocket.close()
1977         except Exception:
1978             pass
1979 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-14c2aa7482fb`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2148` `2147                     await pubsub.close()
2148                 except Exception:
2149                     pass
2150 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-0b6f186dce2c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2273` `2272             await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
2273         except Exception:
2274             pass
2275     finally:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-2ce8f38f13fb`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2281` `2280 if __name__ == "__main__":
2281     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible hardcoded password: 'https://oauth2.googleapis.com/token'

- ID: `scanner-09b2f585a13a`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/calendar-service/app/google_calendar.py:12` `11 
12 TOKEN_URL = "https://oauth2.googleapis.com/token"
13 EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-e4d03ebb6065`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/calendar-service/app/main.py:171` `170 if __name__ == "__main__":
171     uvicorn.run("app.main:app", host="0.0.0.0", port=8050, reload=True)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-8bf278253240`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/main.py:528` `527                 data["download_url"] = urlunparse(parsed_base._replace(path=parsed_dl.path))
528     except Exception:
529         pass
530     return data
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-fae1df51ddcd`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/main.py:969` `968     import uvicorn
969     uvicorn.run(app, host="0.0.0.0", port=18888)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-83279bad850d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:34` `33         _parse_meeting_url(url)
34     assert exc_info.value.status_code == 422
35     if fragment:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-a82899f45848`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:36` `35     if fragment:
36         assert fragment.lower() in str(exc_info.value.detail).lower(), (
37             f"Expected '{fragment}' in detail: {exc_info.value.detail}"
38         )
39 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-bff7cadd2391`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:48` `47         r = parse("https://meet.google.com/abc-defg-hij")
48         assert r.platform == "google_meet"
49         assert r.native_meeting_id == "abc-defg-hij"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8b1223516868`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:49` `48         assert r.platform == "google_meet"
49         assert r.native_meeting_id == "abc-defg-hij"
50         assert r.passcode is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-f97800d1e822`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:50` `49         assert r.native_meeting_id == "abc-defg-hij"
50         assert r.passcode is None
51         assert r.warnings == []
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-c02fceeac6a3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:51` `50         assert r.passcode is None
51         assert r.warnings == []
52 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-1d5fd5cc404e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:55` `54         r = parse("https://meet.google.com/abc-defg-hij?authuser=0&hs=pCv")
55         assert r.native_meeting_id == "abc-defg-hij"
56 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-d8ff5f43e6bf`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:59` `58         r = parse("https://meet.google.com/our-team-standup")
59         assert r.platform == "google_meet"
60         assert r.native_meeting_id == "our-team-standup"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-e9efb4ca4e5d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:60` `59         assert r.platform == "google_meet"
60         assert r.native_meeting_id == "our-team-standup"
61         assert any("workspace" in w.lower() for w in r.warnings)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-e33ed9988ab3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:61` `60         assert r.native_meeting_id == "our-team-standup"
61         assert any("workspace" in w.lower() for w in r.warnings)
62 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-f99c0782858d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:66` `65         r = parse("https://meet.google.com/ab-cd")
66         assert r.native_meeting_id == "ab-cd"
67 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-08f1811d998e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:85` `84         r = parse("https://teams.live.com/meet/9361792952021?p=abc12345")
85         assert r.platform == "teams"
86         assert r.native_meeting_id == "9361792952021"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-ef1b201c29f2`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:86` `85         assert r.platform == "teams"
86         assert r.native_meeting_id == "9361792952021"
87         assert r.passcode == "abc12345"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-5596a1a82a61`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:87` `86         assert r.native_meeting_id == "9361792952021"
87         assert r.passcode == "abc12345"
88         assert r.teams_base_host is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2363fc7f508a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:88` `87         assert r.passcode == "abc12345"
88         assert r.teams_base_host is None
89         assert r.meeting_url is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8fc1bf0c38f2`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:89` `88         assert r.teams_base_host is None
89         assert r.meeting_url is None
90 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-014d74c4eae4`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:93` `92         r = parse("https://teams.live.com/meet/9361792952021")
93         assert r.native_meeting_id == "9361792952021"
94         assert r.passcode is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-29ec8b9ee056`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:94` `93         assert r.native_meeting_id == "9361792952021"
94         assert r.passcode is None
95         assert any("passcode" in w.lower() for w in r.warnings)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-ca81d1ce9792`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:95` `94         assert r.passcode is None
95         assert any("passcode" in w.lower() for w in r.warnings)
96 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-f794d8f4f423`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:108` `107         r = parse("https://teams.microsoft.com/meet/33749853217630?p=em7xplMpIFquiFGvn8")
108         assert r.platform == "teams"
109         assert r.native_meeting_id == "33749853217630"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-92b0a05eef3f`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:109` `108         assert r.platform == "teams"
109         assert r.native_meeting_id == "33749853217630"
110         assert r.passcode == "em7xplMpIFquiFGvn8"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-210e99129e02`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:110` `109         assert r.native_meeting_id == "33749853217630"
110         assert r.passcode == "em7xplMpIFquiFGvn8"
111         assert r.teams_base_host == "teams.microsoft.com"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-3a525086d884`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:111` `110         assert r.passcode == "em7xplMpIFquiFGvn8"
111         assert r.teams_base_host == "teams.microsoft.com"
112         assert r.meeting_url is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-237057d12a3b`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:112` `111         assert r.teams_base_host == "teams.microsoft.com"
112         assert r.meeting_url is None
113 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-38b5eb41cf7c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:116` `115         r = parse("https://teams.microsoft.com/meet/33749853217630")
116         assert r.teams_base_host == "teams.microsoft.com"
117         assert any("passcode" in w.lower() for w in r.warnings)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-08a88558b0ca`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:117` `116         assert r.teams_base_host == "teams.microsoft.com"
117         assert any("passcode" in w.lower() for w in r.warnings)
118 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2b23b2c1ad43`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:121` `120         r = parse("https://gov.teams.microsoft.us/meet/12345678901234")
121         assert r.platform == "teams"
122         assert r.native_meeting_id == "12345678901234"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2863bf938446`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:122` `121         assert r.platform == "teams"
122         assert r.native_meeting_id == "12345678901234"
123         assert r.teams_base_host == "gov.teams.microsoft.us"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-41917a4307f8`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:123` `122         assert r.native_meeting_id == "12345678901234"
123         assert r.teams_base_host == "gov.teams.microsoft.us"
124 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8e1732645af9`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:127` `126         r = parse("https://dod.teams.microsoft.us/meet/12345678901234")
127         assert r.teams_base_host == "dod.teams.microsoft.us"
128 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-737adf1cf1bf`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:131` `130         r = parse("https://teams.microsoft.com/v2/?meetingjoin=true#/meet/33749853217630?p=em7xplMpIFquiFGvn8&anon=true&deeplinkId=c34d42b3")
131         assert r.platform == "teams"
132         assert r.native_meeting_id == "3374985321`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-c5691032043f`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:132` `131         assert r.platform == "teams"
132         assert r.native_meeting_id == "33749853217630"
133         assert r.passcode == "em7xplMpIFquiFGvn8"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-0b6d9af1431c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:133` `132         assert r.native_meeting_id == "33749853217630"
133         assert r.passcode == "em7xplMpIFquiFGvn8"
134         assert r.teams_base_host == "teams.microsoft.com"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-9170538a7c0e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:134` `133         assert r.passcode == "em7xplMpIFquiFGvn8"
134         assert r.teams_base_host == "teams.microsoft.com"
135 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-84febc1381f2`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:138` `137         r = parse("https://teams.microsoft.com/v2/?meetingjoin=true#/meet/33749853217630")
138         assert r.native_meeting_id == "33749853217630"
139         assert any("passcode" in w.lower() for w in r.warnings)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-68995498f39e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:139` `138         assert r.native_meeting_id == "33749853217630"
139         assert any("passcode" in w.lower() for w in r.warnings)
140 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-7c74413acc2c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:155` `154         r = parse(self.LONG_URL)
155         assert r.platform == "teams"
156         # native_meeting_id is a 16-char hex hash
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-b3052a0aa0b3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:157` `156         # native_meeting_id is a 16-char hex hash
157         assert len(r.native_meeting_id) == 16
158         assert all(c in "0123456789abcdef" for c in r.native_meeting_id)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-48a225ad0f76`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:158` `157         assert len(r.native_meeting_id) == 16
158         assert all(c in "0123456789abcdef" for c in r.native_meeting_id)
159         # raw URL is preserved
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-0cbb14c95d17`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:160` `159         # raw URL is preserved
160         assert r.meeting_url == self.LONG_URL
161         assert r.passcode is None
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-6005a89d2218`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:161` `160         assert r.meeting_url == self.LONG_URL
161         assert r.passcode is None
162         assert any("legacy" in w.lower() for w in r.warnings)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8380d1d37e29`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:162` `161         assert r.passcode is None
162         assert any("legacy" in w.lower() for w in r.warnings)
163 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-acbf36411338`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:167` `166         r2 = parse(self.LONG_URL)
167         assert r1.native_meeting_id == r2.native_meeting_id
168 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-0f51fa79e706`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:172` `171         expected = hashlib.sha256(self.LONG_URL.encode()).hexdigest()[:16]
172         assert r.native_meeting_id == expected
173 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2a87979399a0`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:185` `184         r = parse("https://zoom.us/j/12345678901?pwd=Abc123")
185         assert r.platform == "zoom"
186         assert r.native_meeting_id == "12345678901"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-a597047fbc77`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:186` `185         assert r.platform == "zoom"
186         assert r.native_meeting_id == "12345678901"
187         assert r.passcode == "Abc123"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8c59889077ac`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:187` `186         assert r.native_meeting_id == "12345678901"
187         assert r.passcode == "Abc123"
188 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-65bc2933ef3d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:191` `190         r = parse("https://us02web.zoom.us/j/12345678901")
191         assert r.native_meeting_id == "12345678901"
192 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-195bd76e36f3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:195` `194         r = parse("https://company.zoom.us/j/12345678901?pwd=xyz")
195         assert r.native_meeting_id == "12345678901"
196 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-5399bfcc66de`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:199` `198         r = parse("https://zoom.us/w/98765432101?pwd=abc")
199         assert r.native_meeting_id == "98765432101"
200 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-168bed2dbc5c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:203` `202         r = parse("https://zoom.us/wc/join/12345678901")
203         assert r.native_meeting_id == "12345678901"
204 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-ec667b4e87d8`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:207` `206         r = parse("https://zoom.us/j/123456789")
207         assert r.native_meeting_id == "123456789"
208 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-80a02eb72903`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:211` `210         r = parse("https://frbmeetings.zoomgov.com/j/12345678901?pwd=xyz")
211         assert r.platform == "zoom"
212         assert r.native_meeting_id == "12345678901"
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-67868e3d5fb4`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:212` `211         assert r.platform == "zoom"
212         assert r.native_meeting_id == "12345678901"
213 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-b9ea3a8eea58`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:231` `230     def test_google_meet_standard(self):
231         assert Platform.construct_meeting_url("google_meet", "abc-defg-hij") == "https://meet.google.com/abc-defg-hij"
232 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-63c2cd7e1273`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:234` `233     def test_google_meet_custom_nickname(self):
234         assert Platform.construct_meeting_url("google_meet", "our-standup") == "https://meet.google.com/our-standup"
235 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-4045b743116a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:237` `236     def test_google_meet_invalid(self):
237         assert Platform.construct_meeting_url("google_meet", "INVALID!") is None
238 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-7d9798d0f845`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:241` `240     def test_teams_live_default(self):
241         assert Platform.construct_meeting_url("teams", "9361792952021", "abc12345") == \
242             "https://teams.live.com/meet/9361792952021?p=abc12345"
243 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-d12dbc16e0d8`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:245` `244     def test_teams_live_no_passcode(self):
245         assert Platform.construct_meeting_url("teams", "9361792952021") == \
246             "https://teams.live.com/meet/9361792952021"
247 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-3d5d83ce15e6`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:250` `249     def test_teams_enterprise_short(self):
250         assert Platform.construct_meeting_url("teams", "33749853217630", "xyz", base_host="teams.microsoft.com") == \
251             "https://teams.microsoft.com/meet/33749853217630?p=xyz"`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-fe65937c9003`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:254` `253     def test_teams_gcc(self):
254         assert Platform.construct_meeting_url("teams", "12345678901234", base_host="gov.teams.microsoft.us") == \
255             "https://gov.teams.microsoft.us/meet/12345678901234"
256 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-8db869d84905`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:259` `258     def test_teams_hex_hash_returns_none(self):
259         assert Platform.construct_meeting_url("teams", "a3f7c2d891b04e5f") is None
260 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-1d75e20df55a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:262` `261     def test_teams_invalid_id_returns_none(self):
262         assert Platform.construct_meeting_url("teams", "abc") is None
263 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-7a690e36c730`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:266` `265     def test_zoom_standard(self):
266         assert Platform.construct_meeting_url("zoom", "12345678901", "pwd123") == \
267             "https://zoom.us/j/12345678901?pwd=pwd123"
268 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-4f18eddb9c7d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:270` `269     def test_zoom_no_passcode(self):
270         assert Platform.construct_meeting_url("zoom", "12345678901") == "https://zoom.us/j/12345678901"
271 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-b8e74fec584a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:273` `272     def test_zoom_9_digit(self):
273         assert Platform.construct_meeting_url("zoom", "123456789") == "https://zoom.us/j/123456789"
274 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-c3a83ab3ba5d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:276` `275     def test_zoom_invalid_returns_none(self):
276         assert Platform.construct_meeting_url("zoom", "123") is None
277 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-7136cfd0d76f`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:280` `279     def test_unknown_platform_returns_none(self):
280         assert Platform.construct_meeting_url("unknown_platform", "abc123") is None
281 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2a3dac7f01b6`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:291` `290         mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="ab12")
291         assert mc.passcode == "ab12"
292 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-9f4bde33e796`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:296` `295         mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="IXw5Jh")
296         assert mc.passcode == "IXw5Jh"
297 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-019fcd1dfc38`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:301` `300         mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="A" * 20)
301         assert mc.passcode == "A" * 20
302 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-3ee39f153007`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:324` `323         mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021")
324         assert mc.native_meeting_id == "9361792952021"
325 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.

- ID: `scanner-2745856f5508`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/mcp/test_parse_meeting_url.py:329` `328         mc = MeetingCreate(platform="teams", native_meeting_id="a3f7c2d891b04e5f")
329         assert mc.native_meeting_id == "a3f7c2d891b04e5f"
330 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-4f3931475215`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/collector/endpoints.py:217` `216                         abs_start = abs_start.replace(tzinfo=timezone.utc)
217                 except Exception:
218                     pass
219             abs_end_data = d.get("absolute_end_time")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-7453faa993ec`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/collector/endpoints.py:226` `225                         abs_end = abs_end.replace(tzinfo=timezone.utc)
226                 except Exception:
227                     pass
228 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-e1dea7f89ae5`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:202` `201             # set by the natural progression is the right value.
202         except Exception:
203             pass
204 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-0e25dff7d7e1`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:545` `544                     await redis_client.expire(f"browser_session:{session_token}", 86400)
545             except Exception:
546                 pass
547 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-e79f709dd79e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:558` `557                 created_at = datetime.fromtimestamp(c["created_at"], timezone.utc).isoformat()
558             except Exception:
559                 pass
560 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-3876b080289e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:846` `845                             break
846                     except Exception:
847                         pass
848 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-0d1858802928`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1001` `1000         await publish_meeting_status_change(meeting_id, "requested", redis_client, req.platform.value, native_meeting_id, current_user.id)
1001     except Exception:
1002         pass
1003 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-23f1fe18d7e2`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1025` `1024             user_bot_config = user_data.get("bot_config", {})
1025     except Exception:
1026         pass
1027 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Consider possible security implications associated with the subprocess module.

- ID: `scanner-676e53a572ec`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1937` `1936     from .storage import create_storage_client
1937     import subprocess
1938     import tempfile
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Starting a process with a partial executable path

- ID: `scanner-209af7892828`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1992` `1991             dst_path = src_path.rsplit(".", 1)[0] + ".wav"
1992             result = subprocess.run(
1993                 ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
1994                 capture`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-d66589653852`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1992` `1991             dst_path = src_path.rsplit(".", 1)[0] + ".wav"
1992             result = subprocess.run(
1993                 ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
1994                 capture`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-f4d500487818`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/post_meeting.py:175` `174             await db.commit()
175         except Exception:
176             pass
177         return False
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Consider possible security implications associated with the subprocess module.

- ID: `scanner-965fde698dbd`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/recording_finalizer.py:294` `293     """
294     import subprocess
295     import tempfile
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Starting a process with a partial executable path

- ID: `scanner-063132510480`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/recording_finalizer.py:301` `300     try:
301         proc = subprocess.run(
302             [
303                 "ffmpeg", "-y",
304                 "-loglevel", "error",
305                 "-fflags", "+genpts",
306                 "-i", src_path,
307               `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-45287e27280a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/recording_finalizer.py:301` `300     try:
301         proc = subprocess.run(
302             [
303                 "ffmpeg", "-y",
304                 "-loglevel", "error",
305                 "-fflags", "+genpts",
306                 "-i", src_path,
307               `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Standard pseudo-random generators are not suitable for security/cryptographic purposes.

- ID: `scanner-3c5099780468`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/retry.py:46` `45             if attempt < max_retries and _is_retryable(e):
46                 delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), MAX_DELAY)
47                 tag = f" [{label}]" if label else ""
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Probable insecure usage of temp file/directory.

- ID: `scanner-ca46c59949be`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/storage.py:268` `267     def __init__(self, base_dir: Optional[str] = None):
268         self.base_dir = base_dir or os.environ.get("LOCAL_STORAGE_DIR", "/tmp/vexa-recordings")
269         self.fsync_enabled = os.environ.get("LOCAL_STORAGE_FSYNC", "true").l`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Probable insecure usage of temp file/directory.

- ID: `scanner-350a5552edeb`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/runtime-api/runtime_api/backends/kubernetes.py:88` `87             volume_mounts.append(client.V1VolumeMount(
88                 name="dshm", mount_path="/dev/shm",
89             ))
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Consider possible security implications associated with the subprocess module.

- ID: `scanner-33f240a275ac`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/runtime-api/runtime_api/backends/process.py:16` `15 import signal
16 import subprocess
17 import time
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-e9764c140b29`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/runtime-api/runtime_api/backends/process.py:86` `85             log_handle = open(log_file, "w")
86             proc = subprocess.Popen(
87                 spec.command,
88                 env=env,
89                 stdout=log_handle,
90                 stderr=subprocess.STDOUT,
91      `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-7f8782552a08`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/runtime-api/runtime_api/config.py:41` `40 # Server
41 HOST = os.getenv("HOST", "0.0.0.0")
42 PORT = int(os.getenv("PORT", "8090"))
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible hardcoded password: ''

- ID: `scanner-a7681ddd6dbe`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:237` `236 
237 def _get_state(chat_id: int, user_id: str, token: str = "", tg_user_id: int = 0) -> ChatState:
238     key = (chat_id, user_id)
239     if key not in _states:
240         _states[key] = ChatState(user_id=user_id, tg_user_id=tg_user`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Call to httpx with timeout set to None

- ID: `scanner-93dcd9bedbca`
- Severity: medium
- Confidence: low
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:290` `289             _headers = {"X-API-Key": AGENT_API_TOKEN} if AGENT_API_TOKEN else {}
290         async with httpx.AsyncClient(timeout=None, headers=_headers) as client:
291             async with client.stream("POST", chat_url, json=payload`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-00cec1af9b46`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:410` `409             state.bot_msg_id = msg.message_id
410     except Exception:
411         pass
412 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-0e71fdb2e1c1`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:429` `428                 await bot.send_chat_action(chat_id=chat_id, action="typing")
429             except Exception:
430                 pass
431             await asyncio.sleep(4)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-c79258d0584c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:458` `457             )
458     except Exception:
459         pass
460     if state.stream_task and not state.stream_task.done():
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-8bab4acb5ce0`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:544` `543             )
544     except Exception:
545         pass
546     await update.message.reply_text("Session reset. Files in workspace kept.")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-f567e5be4286`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:978` `977                 await bot.send_chat_action(chat_id=chat_id, action="typing")
978             except Exception:
979                 pass
980             await asyncio.sleep(4)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-008b4c691bcf`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/telegram-bot/bot.py:1047` `1046         # Start trigger API server in background
1047         config = uvicorn.Config(trigger_app, host="0.0.0.0", port=TRIGGER_PORT, log_level="info")
1048         server = uvicorn.Server(config)
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Consider possible security implications associated with the subprocess module.

- ID: `scanner-aaac1fca9b1b`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:379` `378             try:
379                 import subprocess, tempfile
380                 with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp_in:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Starting a process with a partial executable path

- ID: `scanner-f3a8fe8ace79`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:384` `383                 tmp_out_path = tmp_in_path.replace('.webm', '.wav')
384                 result = subprocess.run(
385                     ['ffmpeg', '-y', '-i', tmp_in_path, '-ar', '16000', '-ac', '1', '-f', 'wav', tmp_out_path],
386    `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-b6a887585781`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:384` `383                 tmp_out_path = tmp_in_path.replace('.webm', '.wav')
384                 result = subprocess.run(
385                     ['ffmpeg', '-y', '-i', tmp_in_path, '-ar', '16000', '-ac', '1', '-f', 'wav', tmp_out_path],
386    `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-51c5430004ec`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:577` `576         "main:app",
577         host="0.0.0.0",
578         port=8000,
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

## Smoke Validation

- `scanner-semgrep` error: exit=127
- `scanner-gitleaks` ok: exit=1
- `scanner-trivy` missing: trivy not installed.
- `scanner-osv-scanner` missing: osv-scanner not installed.
- `scanner-syft` missing: syft not installed.
- `scanner-zizmor` missing: zizmor not installed.
- `scanner-actionlint` missing: actionlint not installed.
- `scanner-bandit` ok: exit=1
- `scanner-pip-audit` missing: pip-audit not installed.
