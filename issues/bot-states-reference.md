# Bot States Reference

This document describes all possible states for bots in the Vexa system.

## Overview

Bots have two types of states:
1. **Meeting Status** - Business logic state tracked in the database
2. **Container Status** - Physical container state (Docker/Nomad)

---

## Meeting Status (Database State)

The meeting status represents the business logic state of the bot's lifecycle. This is stored in the `Meeting.status` field in the database.

### All Meeting Status Values

| Status | Value | Description | Source | Terminal? |
|--------|-------|-------------|--------|-----------|
| `REQUESTED` | `"requested"` | Bot request created, waiting to start | User API (`POST /bots`) | No |
| `JOINING` | `"joining"` | Bot container started, attempting to join meeting | Bot callback | No |
| `AWAITING_ADMISSION` | `"awaiting_admission"` | Bot in waiting room, waiting to be admitted | Bot callback | No |
| `ACTIVE` | `"active"` | Bot successfully joined and actively transcribing | Bot callback | No |
| `STOPPING` | `"stopping"` | Stop request received, bot is shutting down | User API (`DELETE /bots`) | No |
| `COMPLETED` | `"completed"` | Bot successfully completed the meeting | Bot callback, User API, Delayed stop | **Yes** |
| `FAILED` | `"failed"` | Bot failed at some stage | Bot callback, Validation errors | **Yes** |

### Status Flow Diagram

```
REQUESTED
    │
    ├─→ JOINING
    │       │
    │       ├─→ AWAITING_ADMISSION
    │       │       │
    │       │       ├─→ ACTIVE
    │       │       │       │
    │       │       │       ├─→ STOPPING ──→ COMPLETED
    │       │       │       │       │
    │       │       │       │       └─→ FAILED
    │       │       │       │
    │       │       │       └─→ COMPLETED
    │       │       │       │
    │       │       │       └─→ FAILED
    │       │       │
    │       │       ├─→ COMPLETED
    │       │       │
    │       │       └─→ FAILED
    │       │
    │       ├─→ COMPLETED
    │       │
    │       └─→ FAILED
    │
    ├─→ COMPLETED (stop before container starts)
    │
    └─→ FAILED (validation error)
```

### Valid Status Transitions

From the `get_valid_status_transitions()` function:

- **REQUESTED** → `JOINING`, `FAILED`, `COMPLETED`, `STOPPING`
- **JOINING** → `AWAITING_ADMISSION`, `FAILED`, `COMPLETED`, `STOPPING`
- **AWAITING_ADMISSION** → `ACTIVE`, `FAILED`, `COMPLETED`, `STOPPING`
- **ACTIVE** → `STOPPING`, `COMPLETED`, `FAILED`
- **STOPPING** → `COMPLETED`, `FAILED`
- **COMPLETED** → (terminal, no transitions)
- **FAILED** → (terminal, no transitions)

### Status Sources

- **User API**: `REQUESTED`, `STOPPING`, `COMPLETED` (via stop_bot)
- **Bot Callback**: `JOINING`, `AWAITING_ADMISSION`, `ACTIVE`, `COMPLETED`, `FAILED`
- **Validation Error**: `FAILED` (from `REQUESTED`)
- **Delayed Stop Finalizer**: `COMPLETED` (safety net when callback missed)

---

## Container Status (Physical State)

The container status represents the physical state of the Docker/Nomad container running the bot.

### Normalized Container Status Values

From `BotStatus.normalized_status` validator:

| Status | Description |
|--------|-------------|
| `Requested` | Container requested but not yet created |
| `Starting` | Container is starting up |
| `Up` | Container is running |
| `Stopping` | Container is being stopped |
| `Exited` | Container has exited (cleanly or with error) |
| `Failed` | Container failed to start or crashed |

**Note**: These are normalized from Docker's status strings (e.g., "Up 5 minutes", "Exited (0)", "Dead", etc.)

### Container Status Mapping

From `get_running_bots_status()` function:
- `status.lower().startswith('up')` → `'Up'`
- `status.lower().startswith('exited')` or `'dead' in status.lower()` → `'Exited'`
- `'restarting' in status.lower()` or `'starting' in status.lower()` → `'Starting'`

---

## Completion Reasons

When a meeting reaches `COMPLETED` status, it includes a `completion_reason`:

| Reason | Value | Description |
|--------|-------|-------------|
| `STOPPED` | `"stopped"` | User stopped via API |
| `VALIDATION_ERROR` | `"validation_error"` | Post bot validation failed |
| `AWAITING_ADMISSION_TIMEOUT` | `"awaiting_admission_timeout"` | Timeout while waiting for admission |
| `AWAITING_ADMISSION_REJECTED` | `"awaiting_admission_rejected"` | Rejected from waiting room |
| `LEFT_ALONE` | `"left_alone"` | Timeout for being alone in meeting |
| `EVICTED` | `"evicted"` | Kicked out from meeting using meeting UI |

---

## Failure Stages

When a meeting reaches `FAILED` status, it includes a `failure_stage`:

| Stage | Value | Description |
|-------|-------|-------------|
| `REQUESTED` | `"requested"` | Failed during request/validation |
| `JOINING` | `"joining"` | Failed while attempting to join |
| `AWAITING_ADMISSION` | `"awaiting_admission"` | Failed while in waiting room |
| `ACTIVE` | `"active"` | Failed while actively transcribing |

---

## State Relationships

### Meeting Status vs Container Status

| Meeting Status | Typical Container Status | Notes |
|----------------|-------------------------|-------|
| `REQUESTED` | `Requested` or `Starting` | Container may not exist yet |
| `JOINING` | `Starting` or `Up` | Container starting/joining |
| `AWAITING_ADMISSION` | `Up` | Container running, in waiting room |
| `ACTIVE` | `Up` | Container running, transcribing |
| `STOPPING` | `Up` or `Stopping` | Stop requested, container may still be running |
| `COMPLETED` | `Exited` or `Stopping` | Container stopped/exited |
| `FAILED` | `Exited` or `Failed` | Container exited with error or failed to start |

### Important Notes

1. **Meeting status is authoritative**: The database `Meeting.status` is the source of truth for business logic
2. **Container status is informational**: Container status helps with debugging but doesn't drive business logic
3. **They can be out of sync**: 
   - Container can be `Exited` but meeting still `ACTIVE` (the bug we fixed!)
   - Container can be `Up` but meeting `COMPLETED` (if manually finalized)
4. **Terminal states**: Once `COMPLETED` or `FAILED`, meeting status never changes

---

## Status Transition Examples

### Successful Flow
```
REQUESTED → JOINING → AWAITING_ADMISSION → ACTIVE → COMPLETED
```

### Stop Before Active
```
REQUESTED → COMPLETED (stop_bot called, no container)
```

### Stop While Active
```
ACTIVE → (stop_bot called) → STAYS ACTIVE → (callback arrives) → COMPLETED
OR
ACTIVE → (stop_bot called) → STAYS ACTIVE → (delayed stop finalizer) → COMPLETED
```

### Failure During Join
```
REQUESTED → JOINING → FAILED (failure_stage: JOINING)
```

### Failure While Active
```
ACTIVE → FAILED (failure_stage: ACTIVE)
```

---

## API Endpoints That Change Status

1. **`POST /bots`** → Sets status to `REQUESTED`
2. **`DELETE /bots/{platform}/{native_meeting_id}`** → Can set to `COMPLETED` (fast paths) or relies on callback
3. **`POST /bots/internal/callback/started`** → Can set to `ACTIVE`
4. **`POST /bots/internal/callback/exited`** → Sets to `COMPLETED` or `FAILED`
5. **`POST /bots/internal/callback/status_change`** → Can set to any status (unified callback)

---

## Monitoring Considerations

When monitoring bot health, check:
1. **Meeting status** in database (business logic state)
2. **Container status** from orchestrator (physical state)
3. **Reconciliation**: Meetings in `ACTIVE` should have containers in `Up` state
4. **Orphaned meetings**: `ACTIVE` meetings with no running containers (should be auto-finalized)

---

## Summary

- **7 meeting statuses**: `REQUESTED`, `JOINING`, `AWAITING_ADMISSION`, `ACTIVE`, `STOPPING`, `COMPLETED`, `FAILED`
- **2 terminal states**: `COMPLETED`, `FAILED`
- **6 container statuses**: `Requested`, `Starting`, `Up`, `Stopping`, `Exited`, `Failed`
- **6 completion reasons**: Various reasons why meetings complete
- **4 failure stages**: Where in the lifecycle failures can occur

The system ensures meetings always reach terminal states through:
- Bot callbacks (primary path)
- Delayed stop finalizer (safety net)
- Immediate finalization (fast paths for early stops)

