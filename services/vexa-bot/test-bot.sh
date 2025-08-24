#!/bin/bash

# Comprehensive vexa-bot testing script
# Tests 20 bots one by one and validates they all pass 20 times in a row

set -e

echo "🤖 Starting comprehensive vexa-bot testing (one bot at a time)..."

# Configuration
CONTAINER_NAME="vexa-bot-test"
IMAGE_NAME="vexa-bot:test"
MEETING_URL="https://meet.google.com/uvn-edao-vyo"
MEETING_ID="wnr-jktr-drt"
TOTAL_ITERATIONS=20

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Statistics tracking
TOTAL_TESTS=0
SUCCESSFUL_TESTS=0
FAILED_TESTS=0
CONSECUTIVE_SUCCESSES=0

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

print_iteration() {
    echo -e "${PURPLE}[ITERATION $1]${NC} $2"
}

# Function to cleanup container
cleanup_container() {
    print_status "Cleaning up test container..."
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
    print_success "Cleanup completed"
}

# Function to check if image needs rebuilding
check_image_rebuild() {
    local source_dir="$1"
    local image_name="$2"
    
    # Check if image exists
    if ! docker images | grep -q "$image_name"; then
        print_status "Image doesn't exist, building..."
        return 0  # Need to build
    fi
    
    # Check if source files are newer than image
    local image_created=$(docker inspect "$image_name" --format='{{.Created}}' 2>/dev/null | cut -d'T' -f1)
    if [ -z "$image_created" ]; then
        print_status "Could not determine image creation time, rebuilding..."
        return 0  # Need to build
    fi
    
    # Convert image creation date to timestamp
    local image_timestamp=$(date -d "$image_created" +%s 2>/dev/null || echo "0")
    
    # Check if any source files are newer than image
    local latest_source=$(find "$source_dir" -name "*.ts" -o -name "*.js" -o -name "*.json" -o -name "Dockerfile" | xargs stat -c %Y 2>/dev/null | sort -n | tail -1)
    
    if [ -n "$latest_source" ] && [ "$latest_source" -gt "$image_timestamp" ]; then
        print_status "Source files are newer than image, rebuilding..."
        return 0  # Need to build
    fi
    
    print_status "Image is up to date, skipping rebuild"
    return 1  # No need to build
}

# Function to validate bot success
validate_bot() {
    local container_name="$1"
    local logs
    logs=$(docker logs "$container_name" 2>&1)
    
    local success_indicators=0
    local failure_indicators=0
    
    # Check for success indicators
    if echo "$logs" | grep -q "Successfully admitted to the meeting"; then
        ((success_indicators++))
    fi
    
    if echo "$logs" | grep -q "Found People button using selector"; then
        ((success_indicators++))
    fi
    
    if echo "$logs" | grep -q "People button not found, but continuing with fallback participant monitoring"; then
        ((success_indicators++))  # Fallback mode is also considered success
    fi
    
    if echo "$logs" | grep -q "segments\|transcription"; then
        ((success_indicators++))
    fi
    
    # Check for failure indicators
    if echo "$logs" | grep -q "Error\|error\|ERROR"; then
        ((failure_indicators++))
    fi
    
    if echo "$logs" | grep -q "Failed to join\|Join failed"; then
        ((failure_indicators++))
    fi
    
    # Bot is considered successful if it has more success indicators than failure indicators
    if [ $success_indicators -gt $failure_indicators ]; then
        return 0  # Success
    else
        return 1  # Failure
    fi
}

# Function to run a single iteration
run_iteration() {
    local iteration="$1"
    print_iteration "$iteration" "Starting iteration $iteration/$TOTAL_ITERATIONS"
    
    # Step 1: Start single bot
    print_status "Starting test bot..."
    local connection_id="test-${iteration}-$(date +%s)"
    
    docker run -d \
        --name "$CONTAINER_NAME" \
        --network vexa_vexa_default \
        -e BOT_CONFIG="{\"platform\":\"google_meet\",\"meetingUrl\":\"$MEETING_URL\",\"botName\":\"TestBot\",\"connectionId\":\"$connection_id\",\"nativeMeetingId\":\"$MEETING_ID\",\"token\":\"test-token\",\"redisUrl\":\"redis://redis:6379/0\",\"container_name\":\"$CONTAINER_NAME\",\"automaticLeave\":{\"waitingRoomTimeout\":30000,\"noOneJoinedTimeout\":60000,\"everyoneLeftTimeout\":10000}}" \
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
    print_status "Waiting for bot to initialize..."
    sleep 15
    
    # Step 3: Monitor and validate bot
    print_status "Monitoring and validating bot..."
    
    if validate_bot "$CONTAINER_NAME"; then
        print_success "Bot: PASSED validation"
        ((SUCCESSFUL_TESTS++))
        ((CONSECUTIVE_SUCCESSES++))
    else
        print_error "Bot: FAILED validation"
        ((FAILED_TESTS++))
        CONSECUTIVE_SUCCESSES=0  # Reset consecutive successes
    fi
    
    ((TOTAL_TESTS++))
    
    # Step 4: Display iteration results
    if [ $CONSECUTIVE_SUCCESSES -gt 0 ]; then
        print_success "Iteration $iteration: PASSED (Consecutive successes: $CONSECUTIVE_SUCCESSES)"
    else
        print_warning "Iteration $iteration: FAILED (Consecutive successes reset to 0)"
    fi
    
    # Step 5: Cleanup container for this iteration
    print_status "Cleaning up container for iteration $iteration..."
    cleanup_container
    
    # Step 6: Wait before next iteration
    if [ $iteration -lt $TOTAL_ITERATIONS ]; then
        print_status "Waiting 30 seconds before next iteration..."
        sleep 30
    fi
    
    return 0
}

# Function to display final results
display_results() {
    echo ""
    echo "=================================================="
    echo "🤖 TEST RESULTS SUMMARY"
    echo "=================================================="
    echo "Total Tests Run: $TOTAL_TESTS"
    echo "Successful Tests: $SUCCESSFUL_TESTS"
    echo "Failed Tests: $FAILED_TESTS"
    echo "Overall Success Rate: $(echo "scale=2; $SUCCESSFUL_TESTS / $TOTAL_TESTS * 100" | bc -l 2>/dev/null || echo "0")%"
    echo "Consecutive Successful Iterations: $CONSECUTIVE_SUCCESSES"
    echo ""
    
    if [ $CONSECUTIVE_SUCCESSES -eq $TOTAL_ITERATIONS ]; then
        print_success "🎉 ALL ITERATIONS PASSED! Test completed successfully!"
        exit 0
    else
        print_error "❌ Test failed. Only $CONSECUTIVE_SUCCESSES/$TOTAL_ITERATIONS iterations passed consecutively."
        exit 1
    fi
}

# Main execution
main() {
    print_status "Starting comprehensive test: $TOTAL_ITERATIONS iterations (one bot at a time)"
    print_status "Meeting URL: $MEETING_URL"
    
    # Check if image needs rebuilding
    local core_dir="$(dirname "$0")/core"
    if check_image_rebuild "$core_dir" "$IMAGE_NAME"; then
        print_status "Building test image..."
        cd "$core_dir"
        docker build -t "$IMAGE_NAME" .
        if [ $? -ne 0 ]; then
            print_error "Failed to build image"
            exit 1
        fi
        print_success "Image built successfully"
    fi
    
    # Set trap to cleanup on exit
    trap cleanup_container EXIT
    
    # Run all iterations
    for iteration in $(seq 1 $TOTAL_ITERATIONS); do
        if ! run_iteration "$iteration"; then
            print_error "Iteration $iteration failed"
            break
        fi
        
        # Check if we've lost too many consecutive successes
        if [ $CONSECUTIVE_SUCCESSES -eq 0 ] && [ $iteration -gt 3 ]; then
            print_error "Too many consecutive failures. Stopping test."
            break
        fi
    done
    
    # Display final results
    display_results
}

# Run main function
main "$@"
