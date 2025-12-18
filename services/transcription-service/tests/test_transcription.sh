#!/bin/bash
# Comprehensive test script for transcription API
# Generates test audio, calls API, and validates response

set -e

API_URL="${API_URL:-http://localhost:8083}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_AUDIO="${TEST_AUDIO:-$SCRIPT_DIR/test_audio.wav}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

print_test() {
    echo -e "${YELLOW}▶ $1${NC}"
}

print_pass() {
    echo -e "${GREEN}✓ PASS: $1${NC}"
    ((TESTS_PASSED++)) || true
}

print_fail() {
    echo -e "${RED}✗ FAIL: $1${NC}"
    ((TESTS_FAILED++)) || true
}

print_info() {
    echo -e "  ℹ $1"
}

echo "=========================================="
echo "  Transcription Service API Test Suite"
echo "=========================================="
echo ""
echo "API URL: $API_URL"
echo ""

# Test 1: Health Check
print_test "Test 1: Health Check Endpoint"
HEALTH_RESPONSE=$(curl -s --max-time 5 "$API_URL/health" || echo "")
if [ -z "$HEALTH_RESPONSE" ]; then
    print_fail "Health endpoint returned empty response"
else
    if echo "$HEALTH_RESPONSE" | python3 -m json.tool > /dev/null 2>&1; then
        STATUS=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))" 2>/dev/null || echo "unknown")
        if [ "$STATUS" = "healthy" ]; then
            print_pass "Health check returned healthy status"
            WORKER_ID=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('worker_id', 'unknown'))" 2>/dev/null || echo "unknown")
            DEVICE=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('device', 'unknown'))" 2>/dev/null || echo "unknown")
            print_info "Worker: $WORKER_ID, Device: $DEVICE"
        else
            print_fail "Health check returned status: $STATUS"
        fi
    else
        print_fail "Health endpoint did not return valid JSON"
    fi
fi
echo ""

# Test 2: Load Balancer Status
print_test "Test 2: Load Balancer Status"
LB_RESPONSE=$(curl -s --max-time 5 "$API_URL/lb-status" || echo "")
if [ -z "$LB_RESPONSE" ]; then
    print_fail "Load balancer status endpoint returned empty"
else
    if echo "$LB_RESPONSE" | grep -q "Load Balancer Status"; then
        print_pass "Load balancer status endpoint working"
    else
        print_fail "Load balancer status endpoint returned unexpected response"
    fi
fi
echo ""

# Test 3: Verify Test Audio File
print_test "Test 3: Verify Test Audio File"
if [ ! -f "$TEST_AUDIO" ]; then
    print_fail "Test audio file not found: $TEST_AUDIO"
    print_info "Please generate it first by running: python3 tests/generate_test_audio.py"
    exit 1
fi

if [ ! -s "$TEST_AUDIO" ]; then
    print_fail "Test audio file is empty: $TEST_AUDIO"
    exit 1
fi

FILE_SIZE=$(du -h "$TEST_AUDIO" 2>/dev/null | cut -f1 || echo "unknown")
print_pass "Test audio file found and valid"
print_info "File: $TEST_AUDIO ($FILE_SIZE)"
echo ""

# Test 4: Transcription API Call
print_test "Test 4: Transcription API Call"
print_info "Sending audio file to transcription endpoint..."
TRANSCRIPTION_RESPONSE=$(curl -s --max-time 90 -X POST "$API_URL/v1/audio/transcriptions" \
    -F "file=@$TEST_AUDIO" \
    -F "model=whisper-1" \
    -F "response_format=verbose_json" \
    -F "timestamp_granularities=segment" 2>&1)

if [ -z "$TRANSCRIPTION_RESPONSE" ]; then
    print_fail "Transcription endpoint returned empty response"
elif echo "$TRANSCRIPTION_RESPONSE" | grep -q "502 Bad Gateway\|502\|Bad Gateway"; then
    print_fail "Received 502 Bad Gateway - service may be down or processing failed"
elif echo "$TRANSCRIPTION_RESPONSE" | grep -q "\"error\""; then
    print_fail "Transcription endpoint returned an error"
    print_info "Response: $(echo "$TRANSCRIPTION_RESPONSE" | head -200)"
else
    # Validate JSON structure
    if echo "$TRANSCRIPTION_RESPONSE" | python3 -m json.tool > /dev/null 2>&1; then
        print_pass "Transcription endpoint returned valid JSON"
        
        # Extract and validate fields
        TEXT=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('text', ''))" 2>/dev/null || echo "")
        LANGUAGE=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('language', ''))" 2>/dev/null || echo "")
        DURATION=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('duration', 0))" 2>/dev/null || echo "0")
        SEGMENTS=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('segments', [])))" 2>/dev/null || echo "0")
        
        print_info "Transcribed text: '${TEXT:0:100}${TEXT:+...}'"
        print_info "Detected language: $LANGUAGE"
        print_info "Duration: ${DURATION}s"
        print_info "Segments: $SEGMENTS"
        
        # Validate required fields
        if [ -z "$TEXT" ]; then
            print_fail "Response missing 'text' field"
        else
            print_pass "Response contains 'text' field"
        fi
        
        if [ -z "$LANGUAGE" ]; then
            print_fail "Response missing 'language' field"
        else
            print_pass "Response contains 'language' field"
        fi
        
        if [ "$DURATION" = "0" ] || [ -z "$DURATION" ]; then
            print_fail "Response missing or invalid 'duration' field"
        else
            print_pass "Response contains valid 'duration' field"
        fi
        
        if [ "$SEGMENTS" = "0" ]; then
            print_fail "Response missing 'segments' array"
        else
            print_pass "Response contains 'segments' array with $SEGMENTS segment(s)"
        fi
        
        # Validate segment structure
        if [ "$SEGMENTS" -gt 0 ]; then
            SEGMENT_TEXT=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; segs = json.load(sys.stdin).get('segments', []); print(segs[0].get('text', '') if segs else '')" 2>/dev/null || echo "")
            SEGMENT_START=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; segs = json.load(sys.stdin).get('segments', []); print(segs[0].get('start', -1) if segs else -1)" 2>/dev/null || echo "-1")
            SEGMENT_END=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; segs = json.load(sys.stdin).get('segments', []); print(segs[0].get('end', -1) if segs else -1)" 2>/dev/null || echo "-1")
            
            if [ -n "$SEGMENT_TEXT" ]; then
                print_pass "First segment contains 'text' field"
            else
                print_fail "First segment missing 'text' field"
            fi
            
            if [ "$SEGMENT_START" != "-1" ] && [ -n "$SEGMENT_START" ]; then
                print_pass "First segment contains 'start' timestamp"
            else
                print_fail "First segment missing 'start' timestamp"
            fi
            
            if [ "$SEGMENT_END" != "-1" ] && [ -n "$SEGMENT_END" ]; then
                print_pass "First segment contains 'end' timestamp"
            else
                print_fail "First segment missing 'end' timestamp"
            fi
            
            # Check for audio_start and audio_end (required by Vexa)
            HAS_AUDIO_START=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; segs = json.load(sys.stdin).get('segments', []); print('yes' if segs and 'audio_start' in segs[0] else 'no')" 2>/dev/null || echo "no")
            HAS_AUDIO_END=$(echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; segs = json.load(sys.stdin).get('segments', []); print('yes' if segs and 'audio_end' in segs[0] else 'no')" 2>/dev/null || echo "no")
            
            if [ "$HAS_AUDIO_START" = "yes" ]; then
                print_pass "Segments contain 'audio_start' field (required by Vexa)"
            else
                print_fail "Segments missing 'audio_start' field (required by Vexa)"
            fi
            
            if [ "$HAS_AUDIO_END" = "yes" ]; then
                print_pass "Segments contain 'audio_end' field (required by Vexa)"
            else
                print_fail "Segments missing 'audio_end' field (required by Vexa)"
            fi
        fi
        
        # Check if transcription makes sense
        if [ -n "$TEXT" ] && [ ${#TEXT} -gt 5 ]; then
            print_pass "Transcription contains meaningful text (length: ${#TEXT} chars)"
        elif [ -n "$TEXT" ]; then
            print_info "Transcription text is very short (may be noise or empty audio)"
        fi
    else
        print_fail "Transcription endpoint did not return valid JSON"
        print_info "Response: $(echo "$TRANSCRIPTION_RESPONSE" | head -200)"
    fi
fi
echo ""

# Test 5: Response Format Validation
print_test "Test 5: Response Format Validation"
if [ -n "$TRANSCRIPTION_RESPONSE" ] && echo "$TRANSCRIPTION_RESPONSE" | python3 -m json.tool > /dev/null 2>&1; then
    # Check for required OpenAI Whisper API fields
    REQUIRED_FIELDS=("text" "language" "duration" "segments")
    for field in "${REQUIRED_FIELDS[@]}"; do
        if echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; data = json.load(sys.stdin); exit(0 if '$field' in data else 1)" 2>/dev/null; then
            print_pass "Response contains required field: $field"
        else
            print_fail "Response missing required field: $field"
        fi
    done
    
    # Check segment structure
    if echo "$TRANSCRIPTION_RESPONSE" | python3 -c "import sys, json; data = json.load(sys.stdin); segs = data.get('segments', []); exit(0 if segs and isinstance(segs, list) else 1)" 2>/dev/null; then
        print_pass "Segments is a valid array"
    else
        print_fail "Segments is not a valid array"
    fi
else
    print_fail "Cannot validate format - response is not valid JSON"
fi
echo ""

# Summary
echo "=========================================="
echo "  Test Summary"
echo "=========================================="
echo -e "${GREEN}Tests Passed: $TESTS_PASSED${NC}"
echo -e "${RED}Tests Failed: $TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
