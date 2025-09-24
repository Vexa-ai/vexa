#!/usr/bin/env node

/**
 * Build script to create browser-utils.global.js bundle
 * This script takes the compiled TypeScript output and creates a browser-compatible bundle
 */

const fs = require('fs');
const path = require('path');

// Read the compiled browser utilities JavaScript file
const browserUtilsPath = path.join(__dirname, 'dist', 'utils', 'browser.js');
const browserUtilsContent = fs.readFileSync(browserUtilsPath, 'utf8');

// Create the browser bundle content using a safe CommonJS wrapper
const browserBundleContent = `
// Browser utilities bundle for Vexa Bot
// This file is injected into browser context via page.addScriptTag()
(function() {
  'use strict';

  // Emulate CommonJS environment for the compiled module
  var exports = {};
  var module = { exports: exports };

  (function(exports, module) {
${browserUtilsContent}
  })(exports, module);

  // Expose utilities on window object for browser context
  var utils = module.exports || {};
  window.VexaBrowserUtils = {
    BrowserAudioService: utils.BrowserAudioService,
    BrowserWhisperLiveService: utils.BrowserWhisperLiveService,
    generateBrowserUUID: utils.generateBrowserUUID
  };

  // Also expose performLeaveAction for platform-specific leave UX
  window.performLeaveAction = function(reason) {
    if (window.logBot) { window.logBot('Platform-specific leave action triggered: ' + String(reason)); }
  };

  try {
    console.log('Vexa Browser Utils loaded successfully:', Object.keys(window.VexaBrowserUtils || {}));
  } catch (e) {}
})();
`;

// Ensure dist directory exists
const distDir = path.join(__dirname, 'dist');
if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

// Write the browser bundle
const outputPath = path.join(distDir, 'browser-utils.global.js');
fs.writeFileSync(outputPath, browserBundleContent);

console.log(`✅ Browser utilities bundle created: ${outputPath}`);
console.log('📦 Bundle includes:');
console.log('  - BrowserAudioService');
console.log('  - BrowserWhisperLiveService');
console.log('  - generateBrowserUUID');
console.log('  - window.VexaBrowserUtils');
console.log('  - window.performLeaveAction');