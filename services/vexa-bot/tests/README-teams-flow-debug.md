# Teams Flow Debug Test

This test script (`teams-flow-debug-test.sh`) is designed to debug and validate the Microsoft Teams meeting flow for the vexa-bot.

## Overview

The test validates the complete Teams meeting flow as documented in `teams_docs/flow.md`:

1. **Step 1**: Navigate to Teams meeting URL
2. **Step 2**: Click "Continue on this browser" button  
3. **Step 3**: Allow cameras and microphones (handle permissions)
4. **Step 4**: Type name and click "Join now" button
5. **Step 5**: Wait for admission (handle waiting room)
6. **Step 6**: Successfully admitted with audio access

## Configuration

- **Meeting URL**: `https://teams.live.com/meet/9398850880426?p=RBZCWdxyp85TpcKna8`
- **Screenshots**: Stored in `teams_docs/screenshots/` directory
- **Container**: `vexa-bot-teams-debug`
- **Bot Name**: `TeamsDebugBot`

## Usage

```bash
# Run the Teams flow debug test
./teams-flow-debug-test.sh
```

## What the Test Does

1. **Starts the bot** with Teams platform configuration
2. **Monitors screenshots** in real-time as the bot progresses through the flow
3. **Analyzes logs** to determine which steps succeeded/failed
4. **Requests user validation** to confirm the bot's actual state
5. **Compares bot assessment vs user assessment** to identify discrepancies
6. **Provides detailed debugging information** for troubleshooting

## Screenshot Monitoring

The test captures screenshots at each step:
- `teams-step-1-initial.png` - Initial page load
- `teams-step-2-after-continue.png` - After clicking "Continue on this browser"
- `teams-step-3-after-permissions.png` - After handling permissions
- `teams-step-4-after-join.png` - After entering name and clicking join
- `teams-step-5-admitted.png` - After being admitted to meeting
- `teams-error.png` - If any errors occur

## User Validation Questions

During the test, you'll be asked to validate:

1. Did the bot successfully click 'Continue on this browser'?
2. Did the bot handle camera/microphone permissions correctly?
3. Did the bot enter its name and click 'Join now'?
4. Is the bot in the waiting room or admitted to the meeting?
5. Does the bot have audio access in the meeting?

## Expected Outcomes

- **complete**: Bot completed entire flow successfully with audio access
- **partial**: Bot completed most steps but has some issues
- **waiting**: Bot is stuck in waiting room
- **failed**: Bot failed to complete the flow
- **unknown**: Current state is unclear

## Troubleshooting

### Common Issues

1. **"Continue on this browser" button not found**
   - Check if the page loaded correctly
   - Verify the Teams URL is valid and accessible

2. **Permission denied**
   - Browser may be blocking camera/microphone access
   - Check browser permissions for the Teams domain

3. **Join button not found**
   - Name field may not have been filled correctly
   - Page may not have loaded the pre-join screen

4. **Admission failed**
   - Bot may be stuck in waiting room
   - Meeting may require manual admission

### Debug Information

The test provides:
- **Step-by-step log analysis** showing which steps succeeded/failed
- **Real-time screenshot monitoring** to see the bot's progress
- **Error count and details** for troubleshooting
- **Flow summary** with success rate

## Cleanup

The test automatically:
- Stops and removes the debug container
- Preserves screenshots in `teams_docs/screenshots/`
- Copies logs to local directory
- Shows summary of captured data

## Integration with Teams Flow

This test validates the restructured `teams.ts` implementation that follows the documented flow in `teams_docs/flow.md`. It ensures that:

- Each step of the flow is properly implemented
- Error handling works correctly
- Screenshots are captured for debugging
- The bot can successfully join Teams meetings with audio access

## Related Files

- `teams_docs/flow.md` - Documented Teams meeting flow
- `teams_docs/screenshots/` - Screenshot storage directory
- `core/src/platforms/teams.ts` - Teams platform implementation
- `admission-debug-test.sh` - Similar test for Google Meet platform



