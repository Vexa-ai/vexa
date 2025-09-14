#!/bin/bash

# Test script for google_old.ts against Google Meet URL
# This script runs the test and provides clear output

echo "🚀 Starting google_old.ts test against Google Meet URL"
echo "📋 Meeting URL: https://meet.google.com/hww-qwui-xah"
echo "🤖 Bot Name: VexaBot-Test"
echo ""

# Change to the correct directory
cd /home/dima/dev/vexa/services/vexa-bot/core

# Create screenshots directory if it doesn't exist
mkdir -p screenshots

# Set environment variables
export REDIS_URL="redis://localhost:6379/0"
export NODE_ENV="test"

# Check if TypeScript dependencies are available
echo "🔍 Checking dependencies..."
if ! command -v ts-node &> /dev/null; then
    echo "❌ ts-node not found. Installing..."
    npm install -g ts-node typescript
fi

# Run the test
echo "🎯 Running google_old.ts test..."
echo ""

npx ts-node src/test-google-old.ts

# Capture exit code
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test completed successfully"
else
    echo "❌ Test failed with exit code: $EXIT_CODE"
fi

echo ""
echo "📸 Screenshots saved in: ./screenshots/"
echo "📝 Check the output above for detailed test results"

exit $EXIT_CODE
