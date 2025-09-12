#!/bin/bash

# Admission Detection Debug Test for vexa-bot
# This test focuses on debugging bot's ability to detect when it's admitted vs waiting
# User will help validate the bot's behavior in real-time

set -e

echo "🔍 Starting Admission Detection Debug Test..."

# Configuration
CONTAINER_NAME="vexa-bot-admission-debug"
IMAGE_NAME="vexa-bot:test"
MEETING_URL="https://meet.google.com/uvn-edao-vyo"
MEETING_ID="wnr-jktr-drt"

# Screenshot configuration
SCREENSHOTS_DIR="./screenshots"
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
    if [ -d "./screenshots" ] && [ "$(ls -A ./screenshots)" ]; then
        print_success "Screenshots saved to ./screenshots/"
        ls -la ./screenshots/
    else
        print_warning "No screenshots were captured"
    fi
    
    print_success "Cleanup completed"
}

# Function to monitor screenshots in real-time
monitor_screenshots() {
    print_status "📸 Monitoring screenshots in real-time..."
    print_status "Screenshots directory: $SCREENSHOTS_DIR"
    
    # Show initial state
    if [ -d "$SCREENSHOTS_DIR" ]; then
        local count=$(ls -1 "$SCREENSHOTS_DIR"/*.png 2>/dev/null | wc -l)
        print_status "Current screenshots: $count files"
        if [ $count -gt 0 ]; then
            ls -la "$SCREENSHOTS_DIR"/*.png 2>/dev/null | head -5
        fi
    else
        print_warning "Screenshots directory not found yet"
    fi
}

# Function to analyze bot logs for admission status
analyze_admission_status() {
    local container_name="$1"
    local logs
    logs=$(docker logs "$container_name" 2>&1)
    
    print_debug "=== BOT LOG ANALYSIS ==="
    
    # Check for admission-related messages
    if echo "$logs" | grep -q "Successfully admitted to the meeting"; then
        print_success "✅ Bot reports: Successfully admitted to the meeting"
    else
        print_warning "❌ Bot does NOT report successful admission"
    fi
    
    # Check for waiting room messages
    if echo "$logs" | grep -q "waiting\|Waiting\|waiting room\|Waiting room"; then
        print_warning "⏳ Bot reports: Waiting room activity detected"
    else
        print_debug "ℹ️  No explicit waiting room messages found"
    fi
    
    # Check for People button detection
    if echo "$logs" | grep -q "Found People button using selector"; then
        print_success "✅ Bot reports: Found People button"
    elif echo "$logs" | grep -q "People button not found"; then
        print_warning "❌ Bot reports: People button not found"
    else
        print_debug "ℹ️  No People button detection messages found"
    fi
    
    # Check for transcription activity
    if echo "$logs" | grep -q "segments\|transcription\|audio"; then
        print_success "✅ Bot reports: Transcription/audio activity"
    else
        print_warning "❌ Bot reports: No transcription/audio activity"
    fi
    
    # Check for errors
    local error_count=$(echo "$logs" | grep -c "Error\|error\|ERROR" || echo "0")
    if [ "$error_count" -gt 0 ]; then
        print_error "❌ Bot reports: $error_count error(s)"
        echo "$logs" | grep "Error\|error\|ERROR" | head -5
    else
        print_success "✅ Bot reports: No errors detected"
    fi
    
    print_debug "=== END LOG ANALYSIS ==="
}

# Function to get user validation
get_user_validation() {
    local iteration="$1"
    print_user_input "🤔 ITERATION $iteration - USER VALIDATION NEEDED"
    print_user_input "Please check the Google Meet session: $MEETING_URL"
    print_user_input ""
    print_user_input "Questions:"
    print_user_input "1. Is the bot actually IN the meeting (admitted)?"
    print_user_input "2. Is the bot in the waiting room?"
    print_user_input "3. Can you see the bot's video/audio?"
    print_user_input "4. Is the bot responding to meeting events?"
    print_user_input ""
    print_user_input "Please answer:"
    print_user_input "  'admitted' - Bot is successfully in the meeting"
    print_user_input "  'waiting' - Bot is in waiting room"
    print_user_input "  'error' - Bot failed to join or has issues"
    print_user_input "  'unknown' - You're not sure"
    print_user_input ""
    read -p "Your assessment: " user_assessment
    
    echo "$user_assessment"
}

# Function to run admission debug test
run_admission_debug() {
    print_status "Starting admission detection debug test..."
    
    # Step 1: Start bot
    print_status "Starting debug bot..."
    local connection_id="admission-debug-$(date +%s)"
    
    # Create screenshots directory
    mkdir -p "$SCREENSHOTS_DIR"
    
    docker run -d \
        --name "$CONTAINER_NAME" \
        --network vexa_dev_vexa_default \
        -v "$(pwd)/$SCREENSHOTS_DIR:$SCREENSHOTS_CONTAINER_DIR" \
        -e BOT_CONFIG="{\"platform\":\"google_meet\",\"meetingUrl\":\"$MEETING_URL\",\"botName\":\"AdmissionDebugBot\",\"connectionId\":\"$connection_id\",\"nativeMeetingId\":\"$MEETING_ID\",\"token\":\"debug-token\",\"redisUrl\":\"redis://redis:6379/0\",\"container_name\":\"$CONTAINER_NAME\",\"automaticLeave\":{\"waitingRoomTimeout\":300000,\"noOneJoinedTimeout\":600000,\"everyoneLeftTimeout\":120000}}" \
        --cap-add=SYS_ADMIN \
        --shm-size=2g \
        "$IMAGE_NAME" > /dev/null 2>&1
    
    if [ $? -eq 0 ]; then
        print_success "Bot started successfully"
    else
        print_error "Failed to start bot"
        return 1
    fi
    
    # Step 2: Wait for bot to initialize
    print_status "Waiting for bot to initialize (30 seconds)..."
    sleep 30
    
    # Step 2.5: Monitor screenshots
    monitor_screenshots
    
    # Step 3: Analyze bot logs
    print_status "Analyzing bot logs..."
    analyze_admission_status "$CONTAINER_NAME"
    
    # Step 4: Get user validation
    print_status "Getting user validation..."
    user_assessment=$(get_user_validation "1")
    
    # Step 5: Compare bot assessment vs user assessment
    print_status "=== COMPARISON RESULTS ==="
    print_debug "Bot's self-assessment: Check logs above"
    print_debug "User's assessment: $user_assessment"
    
    # Step 6: Determine if there's a mismatch
    case "$user_assessment" in
        "admitted")
            print_success "✅ User confirms bot is admitted"
            ;;
        "waiting")
            print_warning "⚠️  User says bot is waiting - Bot may have incorrect admission detection"
            ;;
        "error")
            print_error "❌ User reports bot has errors"
            ;;
        "unknown")
            print_warning "❓ User is unsure - Need more investigation"
            ;;
        *)
            print_error "❌ Invalid user input: $user_assessment"
            ;;
    esac
    
    # Step 7: Show recent logs for debugging
    print_status "=== RECENT BOT LOGS (last 20 lines) ==="
    docker logs --tail 20 "$CONTAINER_NAME" 2>&1 | while read line; do
        print_debug "$line"
    done
    
    # Step 8: Ask if user wants to continue monitoring
    print_user_input "Do you want to continue monitoring this bot for more data? (y/n)"
    read -p "Continue monitoring: " continue_monitoring
    
    if [ "$continue_monitoring" = "y" ] || [ "$continue_monitoring" = "Y" ]; then
        print_status "Continuing to monitor bot for 2 more minutes..."
        sleep 120
        
        print_status "=== FINAL SCREENSHOT MONITORING ==="
        monitor_screenshots
        
        print_status "=== FINAL LOG ANALYSIS ==="
        analyze_admission_status "$CONTAINER_NAME"
        
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
    print_status "Admission Detection Debug Test"
    print_status "Meeting URL: $MEETING_URL"
    print_status "This test will help debug bot's admission detection logic"
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
    run_admission_debug
    
    print_status "=== DEBUG TEST COMPLETED ==="
    print_status "Review the results above to identify admission detection issues"
}

# Run main function
main "$@"
