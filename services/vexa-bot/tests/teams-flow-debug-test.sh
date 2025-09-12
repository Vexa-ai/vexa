#!/bin/bash

# Teams Flow Debug Test for vexa-bot
# This test focuses on debugging bot's ability to follow the Teams meeting flow
# Tests the complete flow: Continue on browser -> Allow permissions -> Type name -> Join -> Wait for admission
# User will help validate the bot's behavior in real-time

set -e

echo "🔍 Starting Teams Flow Debug Test..."

# Configuration
CONTAINER_NAME="vexa-bot-teams-debug"
IMAGE_NAME="vexa-bot:test"
MEETING_URL="https://teams.live.com/meet/9398850880426?p=RBZCWdxyp85TpcKna8"
MEETING_ID="9398850880426"

# Screenshot configuration - store in teams_docs folder
SCREENSHOTS_DIR="../core/src/platforms/teams_docs/screenshots"
SCREENSHOTS_CONTAINER_DIR="/app/screenshots"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_debug() {
    echo -e "${CYAN}[DEBUG]${NC} $1"
}

print_user_input() {
    echo -e "${PURPLE}[USER INPUT NEEDED]${NC} $1"
}

# Function to cleanup container
cleanup_container() {
    print_status "Cleaning up debug container..."
    
    # Screenshots are already available in mounted directory
    print_status "Screenshots are available in real-time at: $SCREENSHOTS_DIR"
    
    # Copy logs
    docker cp "$CONTAINER_NAME:/app/logs" ./logs/ 2>/dev/null || true
    docker cp "$CONTAINER_NAME:/tmp/bot-logs.txt" ./bot-logs.txt 2>/dev/null || true
    
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
    
    # Show screenshot summary
    if [ -d "$SCREENSHOTS_DIR" ] && [ "$(ls -A $SCREENSHOTS_DIR)" ]; then
        print_success "Screenshots saved to $SCREENSHOTS_DIR/"
        ls -la "$SCREENSHOTS_DIR"/
    else
        print_warning "No screenshots were captured"
    fi
    
    print_success "Cleanup completed"
}

# Function to monitor screenshots in real-time
monitor_screenshots() {
    print_status "📸 Monitoring Teams flow screenshots in real-time..."
    print_status "Screenshots directory: $SCREENSHOTS_DIR"
    
    # Create directory if it doesn't exist
    mkdir -p "$SCREENSHOTS_DIR"
    
    # Show initial state
    if [ -d "$SCREENSHOTS_DIR" ]; then
        local count=$(ls -1 "$SCREENSHOTS_DIR"/*.png 2>/dev/null | wc -l)
        print_status "Current screenshots: $count files"
        if [ $count -gt 0 ]; then
            ls -la "$SCREENSHOTS_DIR"/*.png 2>/dev/null | head -10
        fi
    else
        print_warning "Screenshots directory not found yet"
    fi
}

# Function to analyze bot logs for Teams flow status
analyze_teams_flow_status() {
    local container_name="$1"
    local logs
    logs=$(docker logs "$container_name" 2>&1)
    
    print_debug "=== TEAMS FLOW LOG ANALYSIS ==="
    
    # Check for each step of the Teams flow
    local step1_success=false
    local step2_success=false
    local step3_success=false
    local step4_success=false
    local step5_success=false
    local step6_success=false
    
    # Step 1: Navigate to Teams meeting
    if echo "$logs" | grep -q "Step 1: Navigating to Teams meeting"; then
        print_success "✅ Step 1: Successfully navigated to Teams meeting"
        step1_success=true
    else
        print_warning "❌ Step 1: Failed to navigate to Teams meeting"
    fi
    
    # Step 2: Continue on this browser
    if echo "$logs" | grep -q "✅ Found continue-browser button"; then
        print_success "✅ Step 2: Successfully clicked 'Continue on this browser'"
        step2_success=true
    else
        print_warning "❌ Step 2: Failed to click 'Continue on this browser'"
    fi
    
    # Step 3: Allow cameras and microphones
    if echo "$logs" | grep -q "✅ Already past permission stage\|✅ Found permission allow button"; then
        print_success "✅ Step 3: Successfully handled camera/microphone permissions"
        step3_success=true
    else
        print_warning "❌ Step 3: Failed to handle camera/microphone permissions"
    fi
    
    # Step 4: Type name and join
    if echo "$logs" | grep -q "✅ Successfully entered name and clicked join"; then
        print_success "✅ Step 4: Successfully entered name and clicked join"
        step4_success=true
    else
        print_warning "❌ Step 4: Failed to enter name or click join"
    fi
    
    # Step 5: Wait for admission
    if echo "$logs" | grep -q "✅ Successfully admitted to the meeting\|✅ Already in the meeting"; then
        print_success "✅ Step 5: Successfully admitted to the meeting"
        step5_success=true
    else
        print_warning "❌ Step 5: Failed to get admitted to the meeting"
    fi
    
    # Step 6: Audio access
    if echo "$logs" | grep -q "Successfully admitted to Teams meeting with audio access"; then
        print_success "✅ Step 6: Successfully gained audio access"
        step6_success=true
    else
        print_warning "❌ Step 6: Failed to gain audio access"
    fi
    
    # Overall flow assessment
    local total_steps=6
    local successful_steps=0
    
    if [ "$step1_success" = true ]; then ((successful_steps++)); fi
    if [ "$step2_success" = true ]; then ((successful_steps++)); fi
    if [ "$step3_success" = true ]; then ((successful_steps++)); fi
    if [ "$step4_success" = true ]; then ((successful_steps++)); fi
    if [ "$step5_success" = true ]; then ((successful_steps++)); fi
    if [ "$step6_success" = true ]; then ((successful_steps++)); fi
    
    print_debug "=== FLOW SUMMARY ==="
    print_debug "Successful steps: $successful_steps/$total_steps"
    
    if [ $successful_steps -eq $total_steps ]; then
        print_success "🎉 COMPLETE SUCCESS: All Teams flow steps completed successfully!"
    elif [ $successful_steps -ge 4 ]; then
        print_warning "⚠️  PARTIAL SUCCESS: Most steps completed, but some issues remain"
    else
        print_error "❌ MAJOR ISSUES: Multiple steps failed in Teams flow"
    fi
    
    # Check for specific Teams-related errors
    if echo "$logs" | grep -q "continue_button_not_found"; then
        print_error "❌ Critical: 'Continue on this browser' button not found"
    fi
    
    if echo "$logs" | grep -q "permission_denied"; then
        print_error "❌ Critical: Camera/microphone permission denied"
    fi
    
    if echo "$logs" | grep -q "join_failed"; then
        print_error "❌ Critical: Failed to join meeting"
    fi
    
    if echo "$logs" | grep -q "admission_failed"; then
        print_error "❌ Critical: Failed to get admitted to meeting"
    fi
    
    # Check for waiting room activity
    if echo "$logs" | grep -q "waiting room\|Waiting room\|Someone will let you in shortly"; then
        print_warning "⏳ Bot reports: Waiting room activity detected"
    fi
    
    # Check for general errors
    local error_count=$(echo "$logs" | grep -c "Error\|error\|ERROR" || echo "0")
    if [ "$error_count" -gt 0 ]; then
        print_error "❌ Bot reports: $error_count error(s)"
        echo "$logs" | grep "Error\|error\|ERROR" | head -5
    else
        print_success "✅ Bot reports: No errors detected"
    fi
    
    print_debug "=== END TEAMS FLOW ANALYSIS ==="
}

# Function to get user validation for Teams flow
get_user_validation() {
    local iteration="$1"
    print_user_input "🤔 ITERATION $iteration - TEAMS FLOW VALIDATION NEEDED"
    print_user_input "Please check the Teams meeting: $MEETING_URL"
    print_user_input ""
    print_user_input "Teams Flow Validation Questions:"
    print_user_input "1. Did the bot successfully click 'Continue on this browser'?"
    print_user_input "2. Did the bot handle camera/microphone permissions correctly?"
    print_user_input "3. Did the bot enter its name and click 'Join now'?"
    print_user_input "4. Is the bot in the waiting room or admitted to the meeting?"
    print_user_input "5. Does the bot have audio access in the meeting?"
    print_user_input ""
    print_user_input "Please answer:"
    print_user_input "  'complete' - Bot completed entire flow successfully with audio access"
    print_user_input "  'partial' - Bot completed most steps but has some issues"
    print_user_input "  'waiting' - Bot is stuck in waiting room"
    print_user_input "  'failed' - Bot failed to complete the flow"
    print_user_input "  'unknown' - You're not sure about the current state"
    print_user_input ""
    read -p "Your assessment: " user_assessment
    
    echo "$user_assessment"
}

# Function to run Teams flow debug test
run_teams_flow_debug() {
    print_status "Starting Teams flow debug test..."
    print_status "Meeting URL: $MEETING_URL"
    
    # Step 1: Start bot
    print_status "Starting Teams debug bot..."
    local connection_id="teams-debug-$(date +%s)"
    
    # Create screenshots directory
    mkdir -p "$SCREENSHOTS_DIR"
    
    docker run -d \
        --name "$CONTAINER_NAME" \
        --network vexa_dev_vexa_default \
        -v "$(pwd)/$SCREENSHOTS_DIR:$SCREENSHOTS_CONTAINER_DIR" \
        -e BOT_CONFIG="{\"platform\":\"teams\",\"meetingUrl\":\"$MEETING_URL\",\"botName\":\"TeamsDebugBot\",\"connectionId\":\"$connection_id\",\"nativeMeetingId\":\"$MEETING_ID\",\"token\":\"debug-token\",\"redisUrl\":\"redis://redis:6379/0\",\"container_name\":\"$CONTAINER_NAME\",\"automaticLeave\":{\"waitingRoomTimeout\":300000,\"noOneJoinedTimeout\":600000,\"everyoneLeftTimeout\":120000}}" \
        --cap-add=SYS_ADMIN \
        --shm-size=2g \
        "$IMAGE_NAME" > /dev/null 2>&1
    
    if [ $? -eq 0 ]; then
        print_success "Teams bot started successfully"
    else
        print_error "Failed to start Teams bot"
        return 1
    fi
    
    # Step 2: Wait for bot to initialize and start flow
    print_status "Waiting for bot to initialize and start Teams flow (45 seconds)..."
    sleep 45
    
    # Step 2.5: Monitor screenshots
    monitor_screenshots
    
    # Step 3: Analyze bot logs
    print_status "Analyzing Teams flow logs..."
    analyze_teams_flow_status "$CONTAINER_NAME"
    
    # Step 4: Get user validation
    print_status "Getting user validation for Teams flow..."
    user_assessment=$(get_user_validation "1")
    
    # Step 5: Compare bot assessment vs user assessment
    print_status "=== TEAMS FLOW COMPARISON RESULTS ==="
    print_debug "Bot's self-assessment: Check logs above"
    print_debug "User's assessment: $user_assessment"
    
    # Step 6: Determine if there's a mismatch
    case "$user_assessment" in
        "complete")
            print_success "✅ User confirms bot completed entire Teams flow successfully"
            ;;
        "partial")
            print_warning "⚠️  User says bot completed most steps but has some issues"
            ;;
        "waiting")
            print_warning "⚠️  User says bot is waiting - May need manual admission"
            ;;
        "failed")
            print_error "❌ User reports bot failed to complete Teams flow"
            ;;
        "unknown")
            print_warning "❓ User is unsure - Need more investigation"
            ;;
        *)
            print_error "❌ Invalid user input: $user_assessment"
            ;;
    esac
    
    # Step 7: Show recent logs for debugging
    print_status "=== RECENT TEAMS BOT LOGS (last 30 lines) ==="
    docker logs --tail 30 "$CONTAINER_NAME" 2>&1 | while read line; do
        print_debug "$line"
    done
    
    # Step 8: Ask if user wants to continue monitoring
    print_user_input "Do you want to continue monitoring this Teams bot for more data? (y/n)"
    read -p "Continue monitoring: " continue_monitoring
    
    if [ "$continue_monitoring" = "y" ] || [ "$continue_monitoring" = "Y" ]; then
        print_status "Continuing to monitor Teams bot for 3 more minutes..."
        sleep 180
        
        print_status "=== FINAL SCREENSHOT MONITORING ==="
        monitor_screenshots
        
        print_status "=== FINAL TEAMS FLOW ANALYSIS ==="
        analyze_teams_flow_status "$CONTAINER_NAME"
        
        print_user_input "Final assessment after extended monitoring:"
        read -p "Final assessment: " final_assessment
        print_debug "Final user assessment: $final_assessment"
    fi
    
    # Step 9: Cleanup
    print_status "Cleaning up..."
    cleanup_container
    
    return 0
}

# Main execution
main() {
    print_status "Teams Flow Debug Test"
    print_status "Meeting URL: $MEETING_URL"
    print_status "This test will help debug bot's Teams meeting flow logic"
    print_status "Screenshots will be stored in: $SCREENSHOTS_DIR"
    print_status ""
    
    # Check if image exists
    if ! docker images | grep -q "$IMAGE_NAME"; then
        print_status "Image doesn't exist, building..."
        cd "$(dirname "$0")/../core"
        docker build -t "$IMAGE_NAME" .
        if [ $? -ne 0 ]; then
            print_error "Failed to build image"
            exit 1
        fi
        print_success "Image built successfully"
    fi
    
    # Set trap to cleanup on exit
    trap cleanup_container EXIT
    
    # Run the debug test
    run_teams_flow_debug
    
    print_status "=== TEAMS FLOW DEBUG TEST COMPLETED ==="
    print_status "Review the results above to identify Teams flow issues"
    print_status "Screenshots are available in: $SCREENSHOTS_DIR"
}

# Run main function
main "$@"
