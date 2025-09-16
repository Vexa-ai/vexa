# Bot Manager Status System Update

## Overview
Updated the bot-manager service to use the new meeting status system with proper validation, data enrichment, and status transitions.

## Key Changes

### 1. Import Updates
```python
from shared_models.schemas import (
    MeetingCreate, MeetingResponse, Platform, BotStatusResponse, MeetingConfigUpdate,
    MeetingStatus, MeetingCompletionReason, MeetingFailureStage,
    is_valid_status_transition, get_status_source
)
```

### 2. Concurrency Limit Check
Updated to include all active states:
```python
Meeting.status.in_([
    MeetingStatus.REQUESTED.value,
    MeetingStatus.JOINING.value,
    MeetingStatus.AWAITING_ADMISSION.value,
    MeetingStatus.ACTIVE.value
])
```

### 3. Status Transition Helper Function
Added `update_meeting_status()` function that:
- Validates status transitions using `is_valid_status_transition()`
- Enriches data field with completion reasons, failure stages, and error details
- Adds status transition metadata
- Handles timestamp updates automatically

### 4. Bot Creation
- Uses `MeetingStatus.REQUESTED.value` as initial status
- Maintains existing validation logic

### 5. Bot Configuration
- Still requires `MeetingStatus.ACTIVE.value` status
- No changes to business logic

### 6. Bot Stop Logic
- Updated to use `MeetingStatus.COMPLETED.value`
- Uses `update_meeting_status()` helper with `MeetingCompletionReason.STOPPED`
- Removed old 'stopping' status logic

### 7. Bot Startup Callback
- Validates status transitions before updating
- Uses `update_meeting_status()` helper
- Handles container ID and start time updates
- Publishes status changes via Redis

### 8. Bot Exit Callback
- Uses `update_meeting_status()` helper for both success and failure cases
- Success: `MeetingStatus.COMPLETED` with `MeetingCompletionReason.STOPPED`
- Failure: `MeetingStatus.FAILED` with `MeetingFailureStage.ACTIVE` and error details

## Status Flow Implementation

### Requested → Active
- Bot startup callback transitions from `requested` to `active`
- Validates transition before updating
- Sets container ID and start time

### Active → Completed
- User stop API: `MeetingCompletionReason.STOPPED`
- Bot exit callback (exit_code=0): `MeetingCompletionReason.STOPPED`

### Active → Failed
- Bot exit callback (exit_code!=0): `MeetingFailureStage.ACTIVE`

### Any Status → Failed
- Validation errors during bot creation
- Bot startup failures
- Bot runtime failures

## Data Field Enrichment

The `data` JSONB field now includes:
```json
{
  "completion_reason": "stopped",
  "failure_stage": "active", 
  "error_details": "Bot exited with code 1",
  "status_transition": {
    "from": "requested",
    "to": "active",
    "timestamp": "2025-01-15T10:30:00Z",
    "source": "bot_callback"
  }
}
```

## Benefits

1. **Consistent Status Management**: All status updates go through validation
2. **Rich Error Context**: Detailed failure and completion reasons
3. **Audit Trail**: Status transition metadata for debugging
4. **Type Safety**: Enum-based status values prevent typos
5. **Business Logic Integrity**: Concurrency limits use proper active states

## Migration Notes

- No database migration required (status column remains String)
- Existing status values continue to work
- New status values provide better granularity
- Status validation prevents invalid transitions
