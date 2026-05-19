# Security Advisory Draft

Status: private draft
Target: `/home/dima/dev/vexa-260508-v0.10.6.1`

## Repository Profile

- Languages: javascript, python, typescript
- Package managers: npm, pip
- API clients: anthropic, fetch, got, httpx, openai, requests, stripe
- Route hints: /, /analytics/meetings, /analytics/meetings/{meeting_id}/telematics, /analytics/users, /analytics/users/{user_id}/details, /api/chat, /api/chat/reset, /api/schedule, /api/sessions, /api/sessions/{session_id}, /api/workspace/file, /api/workspace/files, /api/workspaces, /api/workspaces/{name}, /api/workspaces/{name}/file, /api/workspaces/{name}/files, /auth/me, /b/{token}, /b/{token}/save, /b/{token}/storage

## Findings

### Possible hardcoded secret

- ID: `secret-676ec18c6f`
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

- ID: `timeout-df253507e2`
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

- ID: `timeout-7360669dca`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/api-gateway/main.py:1719` `const res = await fetch('/b/' + TOKEN + '/save', {{ method: 'POST' }});`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-46c7870035`
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

- ID: `timeout-5c9c1893cb`
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

- ID: `timeout-03033d0b31`
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

- ID: `timeout-ebfacdf237`
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

- ID: `timeout-91d4be82bd`
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

- ID: `timeout-9860ace4b6`
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

- ID: `timeout-54e0a8a282`
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

- ID: `timeout-f24b3cec38`
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

- ID: `timeout-f1e83f092d`
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

- ID: `timeout-3813f7765c`
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

- ID: `timeout-226cc1136b`
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

- ID: `timeout-4449152ec6`
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

- ID: `timeout-65e761c7e2`
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

- ID: `timeout-3ec6c2b661`
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

- ID: `timeout-c417465666`
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

- ID: `timeout-3f8bbbe871`
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

- ID: `timeout-47688ebe33`
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

- ID: `timeout-524a1ac673`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:62` `fetch(withBasePath("/api/config"))`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-242d41c483`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:126` `const response = await fetch(withBasePath(`/b/${token}/save`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-944f656043`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:142` `const response = await fetch(withBasePath(`/b/${token}/storage`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b39f430b6e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/meetings/browser-session-view.tsx:157` `const response = await fetch(withBasePath(`/api/vexa/bots/browser_session/${meeting.platform_specific_id}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-787c544e41`
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

- ID: `timeout-a9f05ca928`
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

- ID: `timeout-eb9c0c9ff3`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/components/transcript/transcript-viewer.tsx:561` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2274ba9927`
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

- ID: `timeout-95d2125d10`
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

- ID: `timeout-22e53dc838`
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

- ID: `timeout-bb8efbc6c7`
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

- ID: `timeout-22b58dafd9`
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

- ID: `timeout-0883825a41`
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

- ID: `timeout-6e72115fef`
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

- ID: `timeout-6322854ed2`
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

- ID: `timeout-2cf542dcd6`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:81` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8e63f48e2f`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:102` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d5ec4e2ccd`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:118` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-7c0da3a9a7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:136` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-e392dbdaab`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/agent/[...path]/route.ts:154` `const resp = await fetch(target, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-283189ba2a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/health/route.ts:115` `const response = await fetch(`${adminApiUrl}/admin/users?limit=1`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8fc98a79f1`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/health/route.ts:166` `const response = await fetch(`${vexaApiUrl}/`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-fce16699bd`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:25` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-fc12a39e03`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:43` `const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d595556fbb`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/rotate-secret/route.ts:53` `const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-25447d48db`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/route.ts:37` `const meetingsRes = await fetch(`${VEXA_API_URL}/meetings`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8ee9ebe7ae`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/route.ts:99` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b4149c41bc`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/deliveries/[meetingId]/route.ts:24` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9b87563776`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:30` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8b2d6179a7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:84` `const userRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-958d13606a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:111` `const updateRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-eb8c850ede`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:122` `const putRes = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2575ef1bd3`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/webhooks/config/route.ts:139` `await fetch(`${VEXA_API_URL}/user/webhook`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b522b25a09`
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

- ID: `timeout-d8e1a2fb73`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:45` `const botsResp = await fetch(`${VEXA_API_URL}/bots?${qs.toString()}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-23c3160428`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:60` `const statusResp = await fetch(`${VEXA_API_URL}/bots/status`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-ffff8ad426`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:127` `const response = await fetch(url, { ...fetchOptions, cache: "no-store" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5df9c7a95c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/vexa/[...path]/route.ts:192` `const mediaResponse = await fetch(mediaUrl, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-51b3829408`
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

- ID: `timeout-e3c055c91c`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/auth/send-magic-link/route.ts:43` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-eb8c1315e3`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/auth/me/route.ts:30` `const response = await fetch(`${VEXA_API_URL}/auth/me`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b9d62677a8`
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

- ID: `timeout-542e68742c`
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

- ID: `timeout-596bfb2244`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/route.ts:27` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/users/${userId}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f9e928059d`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/route.ts:84` `const response = await fetch(url, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-2a5c9f3dc0`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/api/profile/keys/[id]/route.ts:28` `const response = await fetch(`${VEXA_ADMIN_API_URL}/admin/tokens/${id}`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-a05b23bcf7`
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

- ID: `timeout-8c3aa3c6a7`
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

- ID: `timeout-6ca22492ca`
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

- ID: `timeout-f61fcf1140`
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

- ID: `timeout-1fc26295ff`
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

- ID: `timeout-b0f8c2ff10`
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

- ID: `timeout-dfd5f0fde7`
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

- ID: `timeout-1d026b038e`
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

- ID: `timeout-c1f0f5c713`
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

- ID: `timeout-2f6fa5429a`
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

- ID: `timeout-a451e2f366`
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

- ID: `timeout-f85ca8962f`
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

- ID: `timeout-f693f019b2`
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

- ID: `timeout-1e365967c2`
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

- ID: `timeout-7cc93abd5b`
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

- ID: `timeout-d248db87d1`
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

- ID: `timeout-14665e4cc3`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:1820` `const response = await fetch(withBasePath(`/b/${sessionToken}/save`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9f47420e89`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:2276` `const response = await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-c4188a4099`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/app/meetings/[id]/page.tsx:2292` `await fetch(`/api/vexa/bots/${platform}/${nativeId}/speak`, { method: "DELETE" });`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f56635925a`
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

- ID: `timeout-7e04ba4cde`
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

- ID: `timeout-fd23b44e35`
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

- ID: `timeout-b13a2d4b8d`
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

- ID: `timeout-1cf3b9f941`
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

- ID: `timeout-1f8aa93d87`
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

- ID: `timeout-a0dcb2df0f`
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

- ID: `timeout-a50a17e2c6`
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

- ID: `timeout-5dd2f21697`
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

- ID: `timeout-7b0ddfc31b`
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

- ID: `timeout-f2abebc944`
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

- ID: `timeout-3349132c8f`
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

- ID: `timeout-cdff1d251c`
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

- ID: `timeout-a8b633c028`
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

- ID: `timeout-e4ee2a3b16`
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

- ID: `timeout-1ba259f0d1`
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

- ID: `timeout-efe10d22a2`
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

- ID: `timeout-7b4d3851e3`
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

- ID: `timeout-f967948320`
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

- ID: `timeout-2ee7f5abeb`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:134` `const response = await fetch(withBasePath("/api/auth/me"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3630136f9e`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/stores/auth-store.ts:153` `const oauthResponse = await fetch(withBasePath("/api/auth/oauth-callback"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-461276d488`
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

- ID: `timeout-a3fcd255ac`
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

- ID: `timeout-ac9e040835`
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

- ID: `timeout-9aa3f2afca`
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

- ID: `timeout-baa100fcd3`
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

- ID: `timeout-835243c296`
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

- ID: `timeout-02a50cc086`
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

- ID: `timeout-dfee1ddd97`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/hooks/use-vexa-websocket.ts:63` `const response = await fetch(withBasePath("/api/config"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-269abab45c`
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

- ID: `timeout-b7ec12dafb`
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

- ID: `timeout-f08cadc717`
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

- ID: `timeout-a6ed0625a6`
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

- ID: `timeout-28d4d7560f`
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

- ID: `timeout-7e2db57760`
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

- ID: `timeout-a3b8419c9a`
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

- ID: `timeout-865154b143`
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

- ID: `timeout-9b8b2c24ec`
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

- ID: `timeout-a3504e8b63`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:120` `const response = await fetch(withBasePath(`/api/vexa/meetings${qs ? `?${qs}` : ""}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-9b1584cb36`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:129` `const response = await fetch(withBasePath(`/api/vexa/meetings/${id}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5520698f35`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:150` `const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}${params}`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-8bf0d10eb3`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:232` `const response = await fetch(withBasePath(`/api/vexa/transcripts/${platform}/${nativeId}/share${qs ? `?${qs}` : ""}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-db0a8183e8`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:240` `const response = await fetch(withBasePath("/api/vexa/bots"), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b6ec7787a4`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:250` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-3d791abad9`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:267` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/config`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-5e8d406dad`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:289` `const response = await fetch(withBasePath("/api/vexa/bots/status"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-ae6c0d15fd`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:316` `const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-b39688802a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:326` `const response = await fetch(withBasePath(`/api/vexa/meetings/${platform}/${nativeId}`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-0cece7a9f9`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:349` `const response = await fetch(withBasePath(`/api/vexa/bots/${platform}/${nativeId}/chat`));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-988a3f728a`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:374` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-d81872bd25`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:426` `const response = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-f5af90c2c7`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:472` `const response = await fetch(withBasePath(`/api/vexa/meetings/${meetingId}/transcribe`), {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-37e57d8d23`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/api.ts:483` `const response = await fetch(withBasePath("/api/vexa/meetings"));`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-e1238bde45`
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

- ID: `timeout-9829ff7dbc`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/auth-utils.ts:26` `const verifyRes = await fetch(`${VEXA_API_URL}/meetings`, {`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-aee8bb3735`
- Severity: medium
- Confidence: low
- Category: api-misuse
- Impact: Missing timeouts can hang workers, exhaust connection pools, and cascade into service outages.
- Evidence: `services/dashboard/src/lib/auth-utils.ts:46` `const res = await fetch(`
- Remediation: Set explicit connect/read timeouts and cancellation behavior for every outbound API call.

Validation strategy:
- Objective: Validate whether an outbound API dependency can stall or return malformed responses without safe handling.
- Owner: machine
- Expected signal: The target fails fast with bounded latency and sanitized error output.

### Outbound API call may be missing a timeout

- ID: `timeout-adf5b8429a`
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

- ID: `timeout-57f2cfae85`
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

- ID: `timeout-f389d06fe3`
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

- ID: `secret-log-00886a51e0`
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

- ID: `secret-log-85da2fc471`
- Severity: medium
- Confidence: low
- Category: data-exposure
- Impact: Secrets in logs can spread to analytics, support tooling, or long-retention log stores.
- Evidence: `services/dashboard/src/hooks/use-vexa-websocket.ts:256` `console.log("WebSocket: Connecting to", wsUrl.replace(/api_key=([^&]+)/, "api_key=***"));`
- Remediation: Redact sensitive fields before logging and add tests for log sanitization.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible secret or token logging

- ID: `secret-log-021924d012`
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

- ID: `secret-log-8b13177c98`
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

- ID: `secret-log-5923200d92`
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

- ID: `secret-log-9d27111ccd`
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

- ID: `secret-log-1fe336fdcf`
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

- ID: `secret-log-94ec1f6ed9`
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

- ID: `scanner-1c70fc375731`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/agent-api/agent_api/main.py:408` `407         hostname = parsed.hostname or ""
408         if hostname in ("localhost", "127.0.0.1", "0.0.0.0") or hostname.endswith(".internal"):
409             raise HTTPException(400, "Cannot schedule requests to internal URLs")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-7ad42a14c8b4`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:294` `293         await app.state.redis.close()
294     except Exception:
295         pass
296 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-bfcacd3eb91b`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:408` `407                 return json.loads(cached)
408         except Exception:
409             pass  # Redis down — fall through to admin-api
410 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-177142e81d12`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:429` `428                     await redis_client.set(cache_key, json.dumps(user_data), ex=60)
429                 except Exception:
430                     pass  # Redis write failure is non-fatal
431             return user_data
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Continue detected.

- ID: `scanner-10c990fe599a`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:938` `937             lines.append(f"[{timestamp}] {speaker}: {text}")
938         except Exception:
939             continue
940 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Continue detected.

- ID: `scanner-19d33c94cc74`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1049` `1048             lines.append(f"[{timestamp}] {speaker}: {text}")
1049         except Exception:
1050             continue
1051 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-0f3060eed394`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1208` `1207                     segments = all_segments[-50:]  # latest 50 segments max
1208             except Exception:
1209                 pass
1210 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-93cbcc357b28`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1612` `1611                             f"browser_session:{token}", updated, ex=86400)
1612                     except Exception:
1613                         pass
1614         except Exception as exc:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-a589a1871f20`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1831` `1830                             await websocket.send_text(message)
1831                 except Exception:
1832                     pass
1833 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-57d3ec5789c1`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1857` `1856             await websocket.close()
1857         except Exception:
1858             pass
1859 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-eeebe4faf1b6`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:1988` `1987                             await websocket.send_bytes(message)
1988                 except Exception:
1989                     pass
1990 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-69c0c4bbe231`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2013` `2012             await websocket.close()
2013         except Exception:
2014             pass
2015 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-a21e8e5ec9c1`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2190` `2189                     await pubsub.close()
2190                 except Exception:
2191                     pass
2192 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-c7862005c35c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2315` `2314             await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
2315         except Exception:
2316             pass
2317     finally:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-f54a35451f89`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/api-gateway/main.py:2323` `2322 if __name__ == "__main__":
2323     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 
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

- ID: `scanner-160aaf256dde`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/collector/endpoints.py:197` `196                         abs_start = abs_start.replace(tzinfo=timezone.utc)
197                 except Exception:
198                     pass
199             abs_end_data = d.get("absolute_end_time")
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-6cb9476ffbb3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/collector/endpoints.py:206` `205                         abs_end = abs_end.replace(tzinfo=timezone.utc)
206                 except Exception:
207                     pass
208 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-8279aaba83fe`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:209` `208             # set by the natural progression is the right value.
209         except Exception:
210             pass
211 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-2b09722a81c2`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:555` `554                     await redis_client.expire(f"browser_session:{session_token}", 86400)
555             except Exception:
556                 pass
557 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-bd81cb754b3c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:568` `567                 created_at = datetime.fromtimestamp(c["created_at"], timezone.utc).isoformat()
568             except Exception:
569                 pass
570 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-d0d090003d20`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:890` `889                             break
890                     except Exception:
891                         pass
892 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-35d517b84c3d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1045` `1044         await publish_meeting_status_change(meeting_id, "requested", redis_client, req.platform.value, native_meeting_id, current_user.id)
1045     except Exception:
1046         pass
1047 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Try, Except, Pass detected.

- ID: `scanner-af1c3d6a3574`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:1069` `1068             user_bot_config = user_data.get("bot_config", {})
1069     except Exception:
1070         pass
1071 
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Consider possible security implications associated with the subprocess module.

- ID: `scanner-38c72f030d54`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:2027` `2026     from .storage import create_storage_client
2027     import subprocess
2028     import tempfile
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Starting a process with a partial executable path

- ID: `scanner-773cd9f2aeb3`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:2054` `2053             dst_path = src_path.rsplit(".", 1)[0] + ".wav"
2054             result = subprocess.run(
2055                 ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
2056                 capture`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-ce3d9589e80d`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/meetings.py:2054` `2053             dst_path = src_path.rsplit(".", 1)[0] + ".wav"
2054             result = subprocess.run(
2055                 ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
2056                 capture`
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

### Try, Except, Pass detected.

- ID: `scanner-e3f8589880ce`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/post_meeting.py:279` `278             await db.rollback()
279         except Exception:
280             pass
281         raise
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

- ID: `scanner-b4b7f24389eb`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/meeting-api/meeting_api/storage.py:237` `236     def __init__(self, base_dir: Optional[str] = None):
237         self.base_dir = base_dir or os.environ.get("LOCAL_STORAGE_DIR", "/tmp/vexa-recordings")
238         self.fsync_enabled = os.environ.get("LOCAL_STORAGE_FSYNC", "true").l`
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

- ID: `scanner-75ce16cafb34`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:432` `431             try:
432                 import subprocess, tempfile
433                 with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp_in:
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Starting a process with a partial executable path

- ID: `scanner-5bfa8ffe274c`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:437` `436                 tmp_out_path = tmp_in_path.replace('.webm', '.wav')
437                 result = subprocess.run(
438                     ['ffmpeg', '-y', '-i', tmp_in_path, '-ar', '16000', '-ac', '1', '-f', 'wav', tmp_out_path],
439    `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### subprocess call - check for execution of untrusted input.

- ID: `scanner-a2c34d24ac3e`
- Severity: low
- Confidence: high
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:437` `436                 tmp_out_path = tmp_in_path.replace('.webm', '.wav')
437                 result = subprocess.run(
438                     ['ffmpeg', '-y', '-i', tmp_in_path, '-ar', '16000', '-ac', '1', '-f', 'wav', tmp_out_path],
439    `
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Possible binding to all interfaces.

- ID: `scanner-67992d7bb95e`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `services/transcription-service/main.py:630` `629         "main:app",
630         host="0.0.0.0",
631         port=8000,
`
- Remediation: Review Bandit guidance and replace the unsafe Python pattern.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-67d4f9ff6639`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:35` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-67d4f9ff6639`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:35` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-f8e44d9f4db5`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:42` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-f8e44d9f4db5`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:42` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-51b30493aed7`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:52` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-51b30493aed7`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:52` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-fd75971c5d07`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:53` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-fd75971c5d07`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:53` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-4158985fc101`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/scripts/mintlify-sync.js:134` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Using QUERY.count() instead of len(QUERY.all()) sends less data to the client since the SQLAlchemy method is performed server-side.

- ID: `scanner-3a6a4f9929d5`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/calendar-service/app/main.py:93` `Using QUERY.count() instead of len(QUERY.all()) sends less data to the client since the SQLAlchemy method is performed server-side.`
- Remediation: Using QUERY.count() instead of len(QUERY.all()) sends less data to the client since the SQLAlchemy method is performed server-side.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities

- ID: `scanner-e9c0bb41a75b`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/auth-validate-final.js:87` `If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities`
- Remediation: If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities

- ID: `scanner-fee5efab2e20`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/auth-validate.js:62` `If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities`
- Remediation: If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities

- ID: `scanner-9b36027a58a6`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/auth-validate3.js:88` `If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities`
- Remediation: If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities

- ID: `scanner-a2b01778eb46`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/deliver-validate.js:21` `If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities`
- Remediation: If unverified user data can reach the `goto` method it can result in Server-Side Request Forgery vulnerabilities
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for th

- ID: `scanner-47d7df5f2030`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/app/api/auth/[...nextauth]/route.ts:134` `Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.`
- Remediation: Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for th

- ID: `scanner-a774f1298131`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/app/api/auth/[...nextauth]/route.ts:141` `Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.`
- Remediation: Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for th

- ID: `scanner-1ef7c0d6d990`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/app/api/auth/[...nextauth]/route.ts:149` `Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.`
- Remediation: Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for th

- ID: `scanner-e0591b32f062`
- Severity: low
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/app/api/auth/[...nextauth]/route.ts:179` `Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.`
- Remediation: Detected string concatenation with a non-literal variable in a util.format / console.log function. If an attacker injects a format specifier in the string, it will forge the log message. Try to use constant values for the format string.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### RegExp() called with a `query` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is reco

- ID: `scanner-3c5d05e923b4`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/components/transcript/transcript-segment.tsx:59` `RegExp() called with a `query` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is recommended to use hardc`
- Remediation: RegExp() called with a `$ARG` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is recommended to use hardcoded regexes instead. If your regex is run on user-controlled input, consider performing input validation or use a regex checking/sanitization library such as https://www.npmjs.com/package/recheck to verify that the regex does not appear vulnerable to ReDoS.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### RegExp() called with a `query` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is reco

- ID: `scanner-bbe5c4830e6d`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/src/components/transcript/transcript-viewer.tsx:67` `RegExp() called with a `query` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is recommended to use hardc`
- Remediation: RegExp() called with a `$ARG` function argument, this might allow an attacker to cause a Regular Expression Denial-of-Service (ReDoS) within your application as RegExP blocks the main thread. For this reason, it is recommended to use hardcoded regexes instead. If your regex is run on user-controlled input, consider performing input validation or use a regex checking/sanitization library such as https://www.npmjs.com/package/recheck to verify that the regex does not appear vulnerable to ReDoS.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Service 'transcription-api' allows for privilege escalation via setuid or setgid binaries. Add 'no-new-privileges:true' in 'security_opt' to prevent this.

- ID: `scanner-0c610a9b658c`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/transcription-service/docker-compose.cpu.yml:6` `Service 'transcription-api' allows for privilege escalation via setuid or setgid binaries. Add 'no-new-privileges:true' in 'security_opt' to prevent this.`
- Remediation: Service '$SERVICE' allows for privilege escalation via setuid or setgid binaries. Add 'no-new-privileges:true' in 'security_opt' to prevent this.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Service 'transcription-api' is running with a writable root filesystem. This may allow malicious applications to download and run additional payloads, or modify container files. If an application inside a container has t

- ID: `scanner-95aa4b159051`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/transcription-service/docker-compose.cpu.yml:6` `Service 'transcription-api' is running with a writable root filesystem. This may allow malicious applications to download and run additional payloads, or modify container files. If an application inside a container has to save something tem`
- Remediation: Service '$SERVICE' is running with a writable root filesystem. This may allow malicious applications to download and run additional payloads, or modify container files. If an application inside a container has to save something temporarily consider using a tmpfs. Add 'read_only: true' to this service to prevent this.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-39d3c518ccda`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/production-replay.test.ts:222` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-39d3c518ccda`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/production-replay.test.ts:222` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-cdbca0a05129`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/production-replay.test.ts:235` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-43de66200373`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/raw-capture.ts:39` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-2b2e171da34a`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/raw-capture.ts:40` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-3ce98de5f814`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/raw-capture.ts:142` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-9268a2fa5916`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/raw-capture.ts:143` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-ba1004b6113a`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/recording.ts:30` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-a8e485eedcbb`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/recording.ts:72` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Inst

- ID: `scanner-92a3374b29b1`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/core/src/services/video-recording.ts:48` `Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sani`
- Remediation: Detected possible user input going into a `path.join` or `path.resolve` function. This could possibly lead to a path traversal vulnerability,  where the attacker can access arbitrary files stored in the file system. Instead, be sure to sanitize or validate user input first.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### If unverified user data can reach the `evaluate` method it can result in Server-Side Request Forgery vulnerabilities

- ID: `scanner-d814c8a030d2`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/hot-debug.js:133` `If unverified user data can reach the `evaluate` method it can result in Server-Side Request Forgery vulnerabilities`
- Remediation: If unverified user data can reach the `evaluate` method it can result in Server-Side Request Forgery vulnerabilities
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### Detected SHA1 hash algorithm which is considered insecure. SHA1 is not collision resistant and is therefore not suitable as a cryptographic signature. Use SHA256 or SHA3 instead.

- ID: `scanner-1e4ba05f6d69`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/lib/human-checklist.py:86` `Detected SHA1 hash algorithm which is considered insecure. SHA1 is not collision resistant and is therefore not suitable as a cryptographic signature. Use SHA256 or SHA3 instead.`
- Remediation: Detected SHA1 hash algorithm which is considered insecure. SHA1 is not collision resistant and is therefore not suitable as a cryptographic signature. Use SHA256 or SHA3 instead.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

### The special variable IFS affects how splitting takes place when expanding unquoted variables. Don't set it globally. Prefer a dedicated utility such as 'cut' or 'awk' if you need to split input data. If you must use 'rea

- ID: `scanner-d3b7190dd8dd`
- Severity: medium
- Confidence: medium
- Category: sast
- Impact: The scanner flagged a code pattern associated with exploitable behavior.
- Evidence: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/synthetic/run-all.sh:148` `The special variable IFS affects how splitting takes place when expanding unquoted variables. Don't set it globally. Prefer a dedicated utility such as 'cut' or 'awk' if you need to split input data. If you must use 'read', set IFS locally `
- Remediation: The special variable IFS affects how splitting takes place when expanding unquoted variables. Don't set it globally. Prefer a dedicated utility such as 'cut' or 'awk' if you need to split input data. If you must use 'read', set IFS locally using e.g. 'IFS="," read -a my_array'.
💎 Enable cross-file analysis and Pro rules for free at sg.run/pro

Validation strategy:
- Objective: Validate exploitability and operational damage with a creative destructive attack against an isolated local target.
- Owner: machine
- Expected signal: The target rejects unsafe input and emits no sensitive data.

## Smoke Validation

- `scanner-semgrep` ok: exit=0
- `scanner-gitleaks` ok: exit=0
- `scanner-trivy` ok: exit=0
- `scanner-osv-scanner` ok: exit=0
- `scanner-syft` ok: exit=0
- `scanner-zizmor` ok: exit=0
- `scanner-actionlint` ok: exit=0
- `scanner-bandit` ok: exit=1
- `scanner-pip-audit` ok: exit=0
