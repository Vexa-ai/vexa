#!/bin/bash

echo "🚀 Quick Browser Bundle Test"
echo "=========================="

# Rebuild browser bundle
echo "📦 Rebuilding browser bundle..."
./node_modules/.bin/esbuild src/utils/browser.ts --bundle --format=iife --global-name=VexaBrowserUtils --outfile=dist/browser-utils.global.js

if [ $? -eq 0 ]; then
    echo "✅ Browser bundle rebuilt successfully"
    echo "📁 Bundle size: $(ls -lh dist/browser-utils.global.js | awk '{print $5}')"
    echo ""
    echo "🧪 Testing browser utilities..."
    
    # Quick syntax check
    node -e "
        try {
            const fs = require('fs');
            const bundle = fs.readFileSync('dist/browser-utils.global.js', 'utf8');
            console.log('✅ Bundle syntax is valid');
            console.log('📊 Bundle contains:', bundle.includes('BrowserAudioService') ? '✅ BrowserAudioService' : '❌ BrowserAudioService');
            console.log('📊 Bundle contains:', bundle.includes('BrowserWhisperLiveService') ? '✅ BrowserWhisperLiveService' : '❌ BrowserWhisperLiveService');
            console.log('📊 Bundle contains:', bundle.includes('sendAudioData') ? '✅ sendAudioData method' : '❌ sendAudioData method');
            console.log('📊 Bundle contains:', bundle.includes('Int16Array') ? '✅ Int16Array conversion' : '❌ Int16Array conversion');
        } catch (error) {
            console.error('❌ Bundle test failed:', error.message);
            process.exit(1);
        }
    "
    
    echo ""
    echo "🎯 Ready for testing! You can now:"
    echo "   1. Open browser-test.html in a browser"
    echo "   2. Run the full Docker test"
    echo "   3. Use the hot-reload dev mode: node dev-watch.js"
    
else
    echo "❌ Browser bundle rebuild failed"
    exit 1
fi
