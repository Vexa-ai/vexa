#!/usr/bin/env node

const { exec } = require('child_process');
const fs = require('fs');
const path = require('path');

console.log('🔥 Starting hot-reload development mode...');

// Watch for changes in browser utilities
const watchPath = path.join(__dirname, 'src/utils/browser.ts');
let isBuilding = false;

function rebuildBrowserBundle() {
  if (isBuilding) return;
  isBuilding = true;
  
  console.log('📦 Rebuilding browser bundle...');
  exec('./node_modules/.bin/esbuild src/utils/browser.ts --bundle --format=iife --global-name=VexaBrowserUtils --outfile=dist/browser-utils.global.js', 
    (error, stdout, stderr) => {
      isBuilding = false;
      if (error) {
        console.error('❌ Build error:', error);
        return;
      }
      console.log('✅ Browser bundle rebuilt successfully');
      console.log('🔄 Ready for next change...');
    }
  );
}

// Initial build
rebuildBrowserBundle();

// Watch for file changes
fs.watchFile(watchPath, { interval: 1000 }, (curr, prev) => {
  if (curr.mtime !== prev.mtime) {
    console.log('📝 Browser utilities changed, rebuilding...');
    rebuildBrowserBundle();
  }
});

console.log('👀 Watching for changes in:', watchPath);
console.log('💡 Make changes to src/utils/browser.ts and they will be automatically rebuilt');
console.log('🛑 Press Ctrl+C to stop');
