#!/bin/bash

# Comprehensive test script for google_old.ts
echo "🚀 Starting comprehensive google_old.ts test"
echo "📋 Meeting URL: https://meet.google.com/hww-qwui-xah"
echo "🤖 Bot Name: VexaBot-Test"
echo ""

# Change to the correct directory
cd /home/dima/dev/vexa/services/vexa-bot/core

# Create screenshots directory if it doesn't exist
mkdir -p screenshots
sudo mkdir -p /app/screenshots
sudo chmod 777 /app/screenshots

# Set environment variables
export REDIS_URL="redis://localhost:6379/0"
export NODE_ENV="test"

# Run the comprehensive test
echo "🎯 Running comprehensive google_old.ts test..."
echo ""

npx ts-node src/test-google-old-comprehensive.ts

# Capture exit code
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Comprehensive test completed successfully"
else
    echo "❌ Comprehensive test failed with exit code: $EXIT_CODE"
fi

echo ""
echo "📸 Screenshots saved in: ./screenshots/ and /app/screenshots/"
echo "📝 Check the output above for detailed test results"

exit $EXIT_CODE
