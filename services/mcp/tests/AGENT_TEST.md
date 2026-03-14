# Agent Test: MCP Service

## Prerequisites
- Services running: mcp, api-gateway (Docker)
- Environment: API_GATEWAY_URL set
- Setup: `docker compose up -d mcp api-gateway`

## Tests

### Test 1: MCP Tool Discovery
**Goal:** Verify the MCP server exposes all expected tools.
**Setup:** Connect to the MCP endpoint and list available tools.
**Verify:** Tools include: request_meeting_bot, get_meeting_transcript, get_bot_status, list_meetings, update_meeting_data, parse_meeting_link, create_transcript_share_link, get_meeting_bundle, list_recordings.
**Pass criteria:** All expected tools are discoverable with correct schemas.

### Test 2: Parse Meeting Link via MCP
**Goal:** Verify parse_meeting_link tool works end-to-end through MCP protocol.
**Setup:** Call the parse_meeting_link tool with a Google Meet URL.
**Verify:** Returns platform=google_meet and correct meeting code.
**Pass criteria:** Parsed output matches expected fields.

### Test 3: Request Bot via MCP
**Goal:** Verify request_meeting_bot tool proxies correctly to api-gateway.
**Setup:** Call request_meeting_bot with a test meeting URL. Verify request reaches api-gateway.
**Verify:** Bot request is forwarded with correct platform/native_meeting_id/passcode.
**Pass criteria:** Bot manager receives the correctly parsed request.

### Test 4: MCP Prompt Templates
**Goal:** Verify all MCP prompts (meeting_prep, during_meeting, post_meeting, teams_link_help) return valid prompt messages.
**Setup:** Call list_prompts, then get_prompt for each one.
**Verify:** Each prompt returns non-empty messages with the expected argument substitution.
**Pass criteria:** All 4 prompts return valid PromptMessage objects.
