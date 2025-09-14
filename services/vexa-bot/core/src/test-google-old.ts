#!/usr/bin/env ts-node

/**
 * Test script for google_old.ts against Google Meet URL
 * Tests the handleGoogleMeet function with the provided meeting URL
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
  
  // Exit with the provided code
  process.exit(exitCode);
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

// Main test function
async function testGoogleOld(): Promise<void> {
  let browser: Browser | null = null;
  let page: Page | null = null;

  try {
    log('🚀 Starting google_old.ts test against Google Meet URL');
    log(`📋 Meeting URL: ${MEETING_URL}`);
    log(`🤖 Bot Name: ${BOT_NAME}`);
    
    // Launch browser
    log('🌐 Launching browser...');
    browser = await chromium.launch({
      headless: true, // Run in headless mode for testing
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-web-security',
        '--disable-features=VizDisplayCompositor',
        '--use-fake-ui-for-media-stream', // Allow media permissions
        '--use-fake-device-for-media-stream', // Use fake media devices
        '--autoplay-policy=no-user-gesture-required', // Allow autoplay
      ]
    });

    // Create new context with user agent and permissions
    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      permissions: ['microphone', 'camera'],
      viewport: { width: 1280, height: 720 }
    });
    
    // Create new page from context
    page = await context.newPage();
    
    log('✅ Browser setup completed');
    
    // Create bot config
    const botConfig = createTestBotConfig();
    log('✅ Bot configuration created');
    
    // Test the handleGoogleMeet function
    log('🎯 Testing handleGoogleMeet function...');
    await handleGoogleMeet(botConfig, page, mockGracefulLeave);
    
    log('✅ Test completed successfully');
    
  } catch (error: any) {
    log(`❌ Test failed with error: ${error.message}`);
    log(`Stack trace: ${error.stack}`);
    
    // Take screenshot on error
    if (page) {
      try {
        await page.screenshot({ 
          path: '/home/dima/dev/vexa/services/vexa-bot/core/screenshots/test-error.png', 
          fullPage: true 
        });
        log('📸 Error screenshot saved');
      } catch (screenshotError: any) {
        log(`Failed to take error screenshot: ${screenshotError.message}`);
      }
    }
    
    throw error;
  } finally {
    // Cleanup
    if (page) {
      try {
        await page.close();
        log('✅ Page closed');
      } catch (error: any) {
        log(`Warning: Error closing page: ${error.message}`);
      }
    }
    
    if (browser) {
      try {
        await browser.close();
        log('✅ Browser closed');
      } catch (error: any) {
        log(`Warning: Error closing browser: ${error.message}`);
      }
    }
  }
}

// Run the test
if (require.main === module) {
  testGoogleOld()
    .then(() => {
      log('🎉 Test completed successfully');
      process.exit(0);
    })
    .catch((error: any) => {
      log(`💥 Test failed: ${error.message}`);
      process.exit(1);
    });
}

export { testGoogleOld };
