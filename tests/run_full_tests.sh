#!/bin/bash

# Vexa Meeting Bot Full Test Suite Runner
# This script demonstrates how to run the complete test suite

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Vexa Meeting Bot Full Test Suite Runner${NC}"
echo "=================================================="

# Check if API key is provided
if [ -z "$1" ]; then
    echo -e "${RED}❌ Error: API key is required${NC}"
    echo "Usage: $0 <API_KEY> [MEETING_URL] [MEETING_URLS...]"
    echo ""
    echo "Examples:"
    echo "  $0 your_api_key_here"
    echo "  $0 your_api_key_here 'https://meet.google.com/abc-defg-hij'"
    echo "  $0 your_api_key_here 'https://meet.google.com/abc-defg-hij' 'https://meet.google.com/url1' 'https://meet.google.com/url2' 'https://meet.google.com/url3'"
    exit 1
fi

API_KEY="$1"
MEETING_URL="${2:-https://meet.google.com/abc-defg-hij}"

echo -e "${YELLOW}📋 Configuration:${NC}"
echo "  API Key: ${API_KEY:0:10}..."
echo "  Meeting URL: $MEETING_URL"

# Build command
CMD="python meeting_bot_suite.py --api-key '$API_KEY' --meeting-url '$MEETING_URL'"

# Add additional meeting URLs for concurrency test if provided
if [ $# -gt 2 ]; then
    shift 2
    CMD="$CMD --meeting-urls"
    for url in "$@"; do
        CMD="$CMD '$url'"
    done
    echo -e "${YELLOW}  Concurrency URLs: $*${NC}"
else
    echo -e "${YELLOW}  Note: No concurrency URLs provided - concurrency test will be skipped${NC}"
fi

echo ""
echo -e "${BLUE}🏃 Running full test suite...${NC}"
echo "Command: $CMD"
echo ""

# Run the test suite
if eval $CMD; then
    echo ""
    echo -e "${GREEN}🎉 All tests completed successfully!${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}💥 Some tests failed. Please review the output above.${NC}"
    exit 1
fi



