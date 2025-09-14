#!/usr/bin/env ts-node

/**
 * Comprehensive test script for google_old.ts against Google Meet URL
 * Tests multiple scenarios and provides detailed analysis
 */

import { chromium, Browser, Page } from 'playwright';
import { handleGoogleMeet } from './platforms/google_old';
import { BotConfig } from './types';
import { log } from './utils';

// Test configuration
const MEETING_URL = 'https://meet.google.com/hww-qwui-xah';
const BOT_NAME = 'VexaBot-Test';

// Mock graceful leave function for testing
const mockGracefulLeave = async (
  page: Page | null, 
  exitCode: number, 
  reason: string, 
  errorDetails?: any
): Promise<void> => {
  log(`🛑 Mock graceful leave called - Exit code: ${exitCode}, Reason: ${reason}`);
  if (errorDetails) {
    log(`Error details: ${JSON.stringify(errorDetails, null, 2)}`);
  }
  
  if (page) {
    try {
      await page.close();
      log('✅ Page closed successfully');
    } catch (error: any) {
      log(`❌ Error closing page: ${error.message}`);
    }
  }
};

// Create bot configuration for testing
const createTestBotConfig = (): BotConfig => {
  return {
    meetingUrl: MEETING_URL,
    botName: BOT_NAME,
    token: 'test-token-12345',
    connectionId: 'test-connection-67890',
    platform: 'google_meet',
    nativeMeetingId: 'hww-qwui-xah',
    language: 'en',
    task: 'transcribe',
    redisUrl: process.env.REDIS_URL || 'redis://redis:6379/0',
    automaticLeave: {
      waitingRoomTimeout: 30000, // 30 seconds for testing
      noOneJoinedTimeout: 60000, // 1 minute for testing
      everyoneLeftTimeout: 300000, // 5 minutes for testing
    },
    reconnectionIntervalMs: 2000,
    meeting_id: 12345,
    botManagerCallbackUrl: undefined,
  };
};

// Test 1: Basic URL validation
async function testUrlValidation(): Promise<boolean> {
  log('🔍 Test 1: URL Validation');
  
  let browser: Browser | null = null;
  let page: Page | null = null;
  
  try {
    browser = await chromium.launch({ headless: true });
    page = await browser.newPage();
    
    log(`📋 Testing URL: ${MEETING_URL}`);
    
    // Navigate to the URL and check what happens
    const response = await page.goto(MEETING_URL, { waitUntil: 'networkidle' });
    
    const finalUrl = page.url();
    const title = await page.title();
    
    log(`📍 Final URL: ${finalUrl}`);
    log(`📄 Page title: ${title}`);
    log(`🔗 Response status: ${response?.status()}`);
    
    // Check if we're on the expected Google Meet page
    const isGoogleMeetPage = finalUrl.includes('meet.google.com') && !finalUrl.includes('workspace.google.com');
    
    if (isGoogleMeetPage) {
      log('✅ URL validation passed - On correct Google Meet page');
      return true;
    } else {
      log('❌ URL validation failed - Redirected to unexpected page');
      log(`Expected: Google Meet page`);
      log(`Actual: ${finalUrl}`);
      return false;
    }
    
  } catch (error: any) {
    log(`❌ URL validation error: ${error.message}`);
    return false;
  } finally {
    if (page) await page.close();
    if (browser) await browser.close();
  }
}

// Test 2: Element detection
async function testElementDetection(): Promise<boolean> {
  log('🔍 Test 2: Element Detection');
  
  let browser: Browser | null = null;
  let page: Page | null = null;
  
  try {
    browser = await chromium.launch({ headless: true });
    page = await browser.newPage();
    
    await page.goto(MEETING_URL, { waitUntil: 'networkidle' });
    await page.waitForTimeout(5000);
    
    // Check for various Google Meet elements
    const elements = [
      'input[type="text"][aria-label="Your name"]',
      'input[aria-label="Your name"]',
      'input[placeholder*="name" i]',
      'input[placeholder*="Name" i]',
      'button[aria-label*="Join"]',
      'button[aria-label*="join"]',
      'button:has-text("Ask to join")',
      'button:has-text("Join now")',
    ];
    
    let foundElements: string[] = [];
    
    for (const selector of elements) {
      try {
        const element = await page.$(selector);
        if (element) {
          foundElements.push(selector);
          log(`✅ Found element: ${selector}`);
        }
      } catch (e) {
        // Element not found, continue
      }
    }
    
    if (foundElements.length > 0) {
      log(`✅ Element detection passed - Found ${foundElements.length} elements`);
      return true;
    } else {
      log('❌ Element detection failed - No expected elements found');
      
      // Take screenshot for debugging
      await page.screenshot({ path: '/app/screenshots/element-detection-failed.png', fullPage: true });
      log('📸 Screenshot saved for debugging');
      
      return false;
    }
    
  } catch (error: any) {
    log(`❌ Element detection error: ${error.message}`);
    return false;
  } finally {
    if (page) await page.close();
    if (browser) await browser.close();
  }
}

// Test 3: Full google_old.ts functionality test
async function testGoogleOldFunctionality(): Promise<boolean> {
  log('🔍 Test 3: Full google_old.ts Functionality');
  
  let browser: Browser | null = null;
  let page: Page | null = null;
  
  try {
    browser = await chromium.launch({ 
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-web-security',
        '--disable-features=VizDisplayCompositor',
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        '--autoplay-policy=no-user-gesture-required',
      ]
    });

    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      permissions: ['microphone', 'camera'],
      viewport: { width: 1280, height: 720 }
    });
    
    page = await context.newPage();
    
    const botConfig = createTestBotConfig();
    
    log('🎯 Testing handleGoogleMeet function...');
    await handleGoogleMeet(botConfig, page, mockGracefulLeave);
    
    log('✅ google_old.ts functionality test completed');
    return true;
    
  } catch (error: any) {
    log(`❌ google_old.ts functionality test failed: ${error.message}`);
    log(`Stack trace: ${error.stack}`);
    
    if (page) {
      try {
        await page.screenshot({ 
          path: '/app/screenshots/functionality-test-error.png', 
          fullPage: true 
        });
        log('📸 Error screenshot saved');
      } catch (screenshotError: any) {
        log(`Failed to take error screenshot: ${screenshotError.message}`);
      }
    }
    
    return false;
  } finally {
    if (page) await page.close();
    if (browser) await browser.close();
  }
}

// Main comprehensive test function
async function runComprehensiveTest(): Promise<void> {
  log('🚀 Starting comprehensive google_old.ts test');
  log(`📋 Meeting URL: ${MEETING_URL}`);
  log(`🤖 Bot Name: ${BOT_NAME}`);
  log('');
  
  const results = {
    urlValidation: false,
    elementDetection: false,
    functionality: false
  };
  
  // Run Test 1: URL Validation
  results.urlValidation = await testUrlValidation();
  log('');
  
  // Run Test 2: Element Detection
  results.elementDetection = await testElementDetection();
  log('');
  
  // Run Test 3: Full Functionality (only if previous tests pass)
  if (results.urlValidation && results.elementDetection) {
    results.functionality = await testGoogleOldFunctionality();
  } else {
    log('⏭️ Skipping functionality test due to previous failures');
  }
  
  // Summary
  log('📊 Test Results Summary:');
  log(`✅ URL Validation: ${results.urlValidation ? 'PASSED' : 'FAILED'}`);
  log(`✅ Element Detection: ${results.elementDetection ? 'PASSED' : 'FAILED'}`);
  log(`✅ Functionality: ${results.functionality ? 'PASSED' : 'FAILED'}`);
  
  const allPassed = results.urlValidation && results.elementDetection && results.functionality;
  
  if (allPassed) {
    log('');
    log('🎉 All tests passed! google_old.ts is working correctly with the provided URL.');
  } else {
    log('');
    log('❌ Some tests failed. Check the details above for troubleshooting.');
    
    if (!results.urlValidation) {
      log('💡 URL Issue: The meeting URL may be invalid, expired, or not accessible.');
    }
    if (!results.elementDetection) {
      log('💡 Element Issue: Google Meet page structure may have changed or the page is not loading correctly.');
    }
    if (!results.functionality) {
      log('💡 Functionality Issue: There may be issues with the google_old.ts implementation or meeting access.');
    }
  }
  
  log('');
  log('📸 Screenshots saved in: /app/screenshots/');
}

// Run the comprehensive test
if (require.main === module) {
  runComprehensiveTest()
    .then(() => {
      log('🏁 Comprehensive test completed');
      process.exit(0);
    })
    .catch((error: any) => {
      log(`💥 Comprehensive test failed: ${error.message}`);
      process.exit(1);
    });
}

export { runComprehensiveTest };
