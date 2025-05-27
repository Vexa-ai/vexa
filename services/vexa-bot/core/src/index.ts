import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { log } from "./utils";
import { chromium } from "playwright-extra";
import { handleGoogleMeet, leaveGoogleMeet } from "./platforms/google";
import { browserArgs, userAgent } from "./constans";
import { BotConfig } from "./types";
import { createClient, RedisClientType } from 'redis';
import { Page, Browser } from 'playwright-core';

// Module-level variables to store current configuration
let currentLanguage: string | null | undefined = null;
let currentTask: string | null | undefined = 'transcribe'; // Default task
let currentRedisUrl: string | null = null;
let currentConnectionId: string | null = null;

// --- ADDED: Flag to prevent multiple shutdowns ---
let isShuttingDown = false;
// ---------------------------------------------

// --- ADDED: Redis subscriber client ---
let redisSubscriber: RedisClientType | null = null;
// -----------------------------------

// --- ADDED: Browser instance ---
let browserInstance: Browser | null = null;
// -------------------------------

// --- ADDED: Message Handler ---
// --- MODIFIED: Make async and add page parameter ---
const handleRedisMessage = async (message: string, channel: string, page: Page | null) => {
  // ++ ADDED: Log entry into handler ++
  log(`[DEBUG] handleRedisMessage entered for channel ${channel}. Message: ${message.substring(0, 100)}...`);
  // ++++++++++++++++++++++++++++++++++
  log(`Received command on ${channel}: ${message}`);
  // --- ADDED: Implement reconfigure command handling --- 
  try {
      const command = JSON.parse(message);
      if (command.action === 'reconfigure') {
          log(`Processing reconfigure command: Lang=${command.language}, Task=${command.task}`);

          // Update Node.js state
          currentLanguage = command.language;
          currentTask = command.task;

          // Trigger browser-side reconfiguration via the exposed function
          if (page && !page.isClosed()) { // Ensure page exists and is open
              try {
                  await page.evaluate(
                      ([lang, task]) => {
                          if (typeof (window as any).triggerWebSocketReconfigure === 'function') {
                              (window as any).triggerWebSocketReconfigure(lang, task);
                          } else {
                              console.error('[Node Eval Error] triggerWebSocketReconfigure not found on window.');
                              // Optionally log via exposed function if available
                              (window as any).logBot?.('[Node Eval Error] triggerWebSocketReconfigure not found on window.');
                          }
                      },
                      [currentLanguage, currentTask] // Pass new config as argument array
                  );
                  log("Sent reconfigure command to browser context via page.evaluate.");
              } catch (evalError: any) {
                  log(`Error evaluating reconfiguration script in browser: ${evalError.message}`);
              }
          } else {
               log("Page not available or closed, cannot send reconfigure command to browser.");
          }
      } else if (command.action === 'leave') {
        // TODO: Implement leave logic (Phase 4)
        log("Received leave command");
        if (!isShuttingDown && page && !page.isClosed()) { // Check flag and page state
          await performGracefulLeave(page);
        } else {
           log("Ignoring leave command: Already shutting down or page unavailable.")
        }
      }
  } catch (e: any) {
      log(`Error processing Redis message: ${e.message}`);
  }
  // -------------------------------------------------
};
// ----------------------------

// --- ADDED: Graceful Leave Function ---
async function performGracefulLeave(page: Page): Promise<void> {
  if (isShuttingDown) {
    log("[Graceful Leave] Already in progress, ignoring duplicate call.");
    return;
  }
  isShuttingDown = true;
  log("[Graceful Leave] Initiating graceful shutdown sequence...");

  let leaveSuccess = false;
  try {
    // Call the appropriate platform-specific leave function
    // Assuming google_meet for now based on previous context
    // TODO: Make this platform-dynamic if supporting multiple platforms
    log("[Graceful Leave] Attempting platform-specific leave...");
    leaveSuccess = await leaveGoogleMeet(page);
    log(`[Graceful Leave] Platform leave attempt result: ${leaveSuccess}`);
  } catch (leaveError: any) {
    log(`[Graceful Leave] Error during platform leave attempt: ${leaveError.message}`);
    leaveSuccess = false; // Ensure it's false on error
  }

  // Close Redis connection (if exists and open)
  if (redisSubscriber && redisSubscriber.isOpen) {
      log("[Graceful Leave] Disconnecting Redis subscriber...");
      try {
          await redisSubscriber.unsubscribe();
          await redisSubscriber.quit();
          log("[Graceful Leave] Redis subscriber disconnected.");
      } catch (err) {
          log(`[Graceful Leave] Error closing Redis connection: ${err}`);
      }
  }

  // Close the browser instance
  log("[Graceful Leave] Closing browser instance...");
  try {
    if (browserInstance && browserInstance.isConnected()) {
       await browserInstance.close();
       log("[Graceful Leave] Browser instance closed.");
    } else {
       log("[Graceful Leave] Browser instance already closed or not available.");
    }
  } catch (browserCloseError: any) {
    log(`[Graceful Leave] Error closing browser: ${browserCloseError.message}`);
  }

  // Exit the process only if the leave attempt was considered successful
  if (leaveSuccess) {
      log("[Graceful Leave] Exiting process with code 0 (Success).");
      process.exit(0);
  } else {
      log("[Graceful Leave] Leave attempt failed or button not found. Exiting process with code 1 (Failure). Waiting for external termination.");
      // We exit with 1 to indicate failure, but the delayed stop from bot-manager will still kill it.
      // If we *didn't* exit here, the process would hang until killed.
      process.exit(1);
  }
}
// --- ----------------------------- ---

// --- ADDED: Function to be called from browser to trigger leave ---
// This needs to be defined in a scope where 'page' will be available when it's exposed.
// We will define the actual exposed function inside runBot where 'page' is in scope.
// --- ------------------------------------------------------------ ---

export async function runBot(botConfig: BotConfig): Promise<void> {
  // --- UPDATED: Parse and store config values ---
  currentLanguage = botConfig.language;
  currentTask = botConfig.task || 'transcribe'; // Use default if null/undefined
  currentRedisUrl = botConfig.redisUrl;
  currentConnectionId = botConfig.connectionId;
  // ---------------------------------------------

  // Destructure other needed config values
  const { meetingUrl, platform, botName } = botConfig;

  log(`Starting bot for ${platform} with URL: ${meetingUrl}, name: ${botName}, language: ${currentLanguage}, task: ${currentTask}, connectionId: ${currentConnectionId}`);

  // --- ADDED: Redis Client Setup and Subscription ---
  if (currentRedisUrl && currentConnectionId) {
    log("Setting up Redis subscriber...");
    try {
      redisSubscriber = createClient({ url: currentRedisUrl });

      redisSubscriber.on('error', (err) => log(`Redis Client Error: ${err}`));
      // ++ ADDED: Log connection events ++
      redisSubscriber.on('connect', () => log('[DEBUG] Redis client connecting...'));
      redisSubscriber.on('ready', () => log('[DEBUG] Redis client ready.'));
      redisSubscriber.on('reconnecting', () => log('[DEBUG] Redis client reconnecting...'));
      redisSubscriber.on('end', () => log('[DEBUG] Redis client connection ended.'));
      // ++++++++++++++++++++++++++++++++++

      await redisSubscriber.connect();
      log(`Connected to Redis at ${currentRedisUrl}`);

      const commandChannel = `bot_commands:${currentConnectionId}`;
      // Pass the page object when subscribing
      // ++ MODIFIED: Add logging inside subscribe callback ++
      await redisSubscriber.subscribe(commandChannel, (message, channel) => {
          log(`[DEBUG] Redis subscribe callback fired for channel ${channel}.`); // Log before handling
          handleRedisMessage(message, channel, page)
      }); 
      // ++++++++++++++++++++++++++++++++++++++++++++++++
      log(`Subscribed to Redis channel: ${commandChannel}`);

    } catch (err) {
      log(`*** Failed to connect or subscribe to Redis: ${err} ***`);
      // Decide how to handle this - exit? proceed without command support?
      // For now, log the error and proceed without Redis.
      redisSubscriber = null; // Ensure client is null if setup failed
    }
  } else {
    log("Redis URL or Connection ID missing, skipping Redis setup.");
  }
  // -------------------------------------------------

  // Use Stealth Plugin to avoid detection
  const stealthPlugin = StealthPlugin();
  stealthPlugin.enabledEvasions.delete("iframe.contentWindow");
  stealthPlugin.enabledEvasions.delete("media.codecs");
  chromium.use(stealthPlugin);

  // Launch browser with stealth configuration
  browserInstance = await chromium.launch({
    headless: false,
    args: browserArgs,
  });

  // Create a new page with permissions and viewport
  const context = await browserInstance.newContext({
    permissions: ["camera", "microphone"],
    userAgent: userAgent,
    viewport: {
      width: 1280,
      height: 720
    }
  })
  const page = await context.newPage();

  // --- ADDED: Expose a function for browser to trigger Node.js graceful leave ---
  await page.exposeFunction("triggerNodeGracefulLeave", async () => {
    log("[Node.js] Received triggerNodeGracefulLeave from browser context.");
    if (!isShuttingDown) { // Check flag to avoid multiple triggers
      await performGracefulLeave(page);
    } else {
      log("[Node.js] Ignoring triggerNodeGracefulLeave as shutdown is already in progress.");
    }
  });
  // --- ----------------------------------------------------------------------- ---

  // Setup anti-detection measures
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    Object.defineProperty(navigator, "plugins", {
      get: () => [{ name: "Chrome PDF Plugin" }, { name: "Chrome PDF Viewer" }],
    });
    Object.defineProperty(navigator, "languages", {
      get: () => ["en-US", "en"],
    });
    Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 4 });
    Object.defineProperty(navigator, "deviceMemory", { get: () => 8 });
    Object.defineProperty(window, "innerWidth", { get: () => 1920 });
    Object.defineProperty(window, "innerHeight", { get: () => 1080 });
    Object.defineProperty(window, "outerWidth", { get: () => 1920 });
    Object.defineProperty(window, "outerHeight", { get: () => 1080 });
  });

  // Switch based on the *external* platform name received in botConfig
  switch (platform) {
    case 'google_meet': // Use external name
      await handleGoogleMeet(botConfig, page)
      break;
    case 'zoom': // External name
      // todo
      //await handleMeet(page);
      break;
    case 'teams': // External name
      // todo
      //  await handleTeams(page);
      break;
    default:
      // Log the unexpected platform value
      log(`Error: Unsupported platform received: ${platform}`);
      throw new Error(`Unsupported platform: ${platform}`);
  }

  log('Bot execution completed OR waiting for external termination/command.'); // Update log message
}

// --- ADDED: Basic Signal Handling (for future Phase 5) ---
// Setup signal handling to also trigger graceful leave
const gracefulShutdown = async (signal: string) => {
    log(`Received signal: ${signal}. Triggering graceful shutdown.`);
    // Need page context here - how to get it? 
    // Option 1: Make page module-level (like browserInstance) - simpler for now
    // Option 2: Track active pages in a map (more complex)
    // Let's assume page is available or handle appropriately if not.
    // This part needs refinement in Phase 5.
    // For now, just log and attempt browser close if no page.
    if (!isShuttingDown) {
        // If page is not easily accessible here, at least close the browser
         log("[Signal Shutdown] Attempting to close browser directly.");
         if (browserInstance && browserInstance.isConnected()) {
            await browserInstance.close();
         }
         process.exit(signal === 'SIGINT' ? 130 : 143); // Standard exit codes for INT/TERM
    } else {
         log("[Signal Shutdown] Shutdown already in progress.");
    }
};

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));
// --- ------------------------------------------------- ---
