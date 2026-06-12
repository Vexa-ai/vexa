#!/usr/bin/env node


/**
 * Build script to create browser-utils.global.js bundle
 * This script takes the compiled TypeScript output and creates a browser-compatible bundle
 */

const fs = require('fs');
const path = require('path');
// capture-kit brick (consumed, not local browser/): resolve its built dist
const CK = path.dirname(require.resolve('@vexa/capture-kit/package.json'));
const CK_DIST = path.join(CK, 'dist');

// Read the compiled browser-side modules (each is a self-contained CommonJS
// file — no cross-file requires — wrapped in its own shim below)
const browserUtilsPath = path.join(__dirname, 'dist', 'utils', 'browser.js');
const browserUtilsContent = fs.readFileSync(browserUtilsPath, 'utf8');
const gmeetSpeakersPath = path.join(CK_DIST, 'gmeet-speakers.js');
const gmeetSpeakersContent = fs.readFileSync(gmeetSpeakersPath, 'utf8');
const zoomSpeakersPath = path.join(CK_DIST, 'zoom-speakers.js');
const zoomSpeakersContent = fs.readFileSync(zoomSpeakersPath, 'utf8');
const gmeetCapturePath = path.join(CK_DIST, 'gmeet-capture.js');
const gmeetCaptureContent = fs.readFileSync(gmeetCapturePath, 'utf8');
const teamsSpeakersPath = path.join(CK_DIST, 'msteams-speakers.js');
const teamsSpeakersContent = fs.readFileSync(teamsSpeakersPath, 'utf8');

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

  var gmExports = {};
  var gmModule = { exports: gmExports };
  (function(exports, module) {
${gmeetSpeakersContent}
  })(gmExports, gmModule);

  var zmExports = {};
  var zmModule = { exports: zmExports };
  (function(exports, module) {
${zoomSpeakersContent}
  })(zmExports, zmModule);

  var gcExports = {};
  var gcModule = { exports: gcExports };
  (function(exports, module) {
${gmeetCaptureContent}
  })(gcExports, gcModule);

  var tsExports = {};
  var tsModule = { exports: tsExports };
  (function(exports, module) {
${teamsSpeakersContent}
  })(tsExports, tsModule);

  // Expose utilities on window object for browser context
  var utils = module.exports || {};
  var gm = gmModule.exports || {};
  var zm = zmModule.exports || {};
  window.VexaBrowserUtils = {
    BrowserAudioService: utils.BrowserAudioService,
    BrowserMediaRecorderPipeline: utils.BrowserMediaRecorderPipeline,
    BrowserWhisperLiveService: utils.BrowserWhisperLiveService,
    generateBrowserUUID: utils.generateBrowserUUID,
    createGmeetSpeakers: gm.createGmeetSpeakers,
    createZoomSpeakers: zm.createZoomSpeakers,
    createGmeetCapture: (gcModule.exports || {}).createGmeetCapture,
    createTeamsSpeakers: (tsModule.exports || {}).createTeamsSpeakers
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
console.log('  - BrowserMediaRecorderPipeline');
console.log('  - BrowserWhisperLiveService');
console.log('  - generateBrowserUUID');
console.log('  - window.VexaBrowserUtils');
console.log('  - window.performLeaveAction');