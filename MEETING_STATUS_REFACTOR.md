# Meeting Status Refactor

## Overview
Refactored meeting status system to provide clearer state transitions, better error handling, and explicit status sources.

## New Status Flow

```
requested -> joining -> awaiting_admission -> active -> completed
                                    |              |
                                    v              v
                                 failed         failed
```

## Status Definitions

### MeetingStatus Enum
- `requested`: Initial status when user creates bot via POST API
- `joining`: Bot is attempting to join the meeting
- `awaiting_admission`: Bot is waiting to be admitted to the meeting
- `active`: Bot is successfully in the meeting and transcribing
- `completed`: Meeting has ended (terminal state)
- `failed`: Meeting failed at some stage (terminal state)

### Status Sources
- **requested**: POST bot API (user)
- **joining**: bot callback
- **awaiting_admission**: bot callback  
- **active**: bot callback
- **completed**: user (stop bot API - PRIORITY!), bot callback
- **failed**: bot callback, validation errors

## Completion Reasons (stored in data.completion_reason)

### MeetingCompletionReason Enum
- `stopped`: User stopped by API
- `validation_error`: Post bot validation failed
- `awaiting_admission_timeout`: Timeout during awaiting admission
- `awaiting_admission_rejected`: Rejected during awaiting admission
- `left_alone`: Timeout for being alone
- `evicted`: Kicked out from meeting using meeting UI

## Failure Stages (stored in data.failure_stage)

### MeetingFailureStage Enum
- `requested`: Failed during initial request
- `joining`: Failed while joining meeting
- `awaiting_admission`: Failed while waiting for admission
- `active`: Failed while active in meeting

## Data Field Structure

The `data` JSONB field now supports:

```json
{
  "name": "Meeting name",
  "participants": ["Alice", "Bob"],
  "languages": ["en", "es"],
  "notes": "Meeting notes",
  "completion_reason": "stopped",  // For completed meetings
  "failure_stage": "joining",      // For failed meetings
  "error_details": "Specific error message"
}
```

## Concurrency Limit Updates

Updated concurrency limit check to include all active states:
- `requested`
- `joining` 
- `awaiting_admission`
- `active`

## Validation

- Status transitions are validated using `is_valid_status_transition()`
- Completion reasons are validated for completed meetings
- Failure stages are validated for failed meetings
- Status sources are tracked for debugging

## Migration Notes

- Database column remains `String(50)` - no migration needed
- Existing status values will continue to work
- New status values should be used going forward
- Status validation is enforced in Pydantic schemas
