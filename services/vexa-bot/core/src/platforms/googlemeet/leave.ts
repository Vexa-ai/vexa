import { Page } from "playwright";
import { log, callLeaveCallback } from "../../utils";
import { BotConfig } from "../../types";
import { googleLeaveSelectors } from "./selectors";

// Prepare for recording by exposing necessary functions
export async function prepareForRecording(page: Page, botConfig: BotConfig): Promise<void> {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Expose bot config for callback functions
  await page.exposeFunction("getBotConfig", (): BotConfig => botConfig);

  // Ensure leave function is available even before admission
  await page.evaluate((selectorsData) => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          // Call leave callback first to notify bot-manager
          (window as any).logBot?.("🔥 Calling leave callback before attempting to leave...");
          try {
            const botConfig = (window as any).getBotConfig?.();
            if (botConfig) {
              // We need to call the callback from Node.js context, not browser context
              // This will be handled by the Node.js side when leaveGoogleMeet is called
              (window as any).logBot?.("📡 Leave callback will be sent from Node.js context");
            }
          } catch (callbackError: any) {
            (window as any).logBot?.(`⚠️ Warning: Could not prepare leave callback: ${callbackError.message}`);
          }

          // Use directly injected selectors (stateless approach)
          const leaveSelectors = selectorsData.googleLeaveSelectors || [];

          (window as any).logBot?.("🔍 Starting stateless Google Meet leave button detection...");
          (window as any).logBot?.(`📋 Will try ${leaveSelectors.length} selectors until one works`);
          
          // Try each selector until one works (stateless iteration)
          for (let i = 0; i < leaveSelectors.length; i++) {
            const selector = leaveSelectors[i];
            try {
              (window as any).logBot?.(`🔍 [${i + 1}/${leaveSelectors.length}] Trying selector: ${selector}`);
              
              const button = document.querySelector(selector) as HTMLElement;
              if (button) {
                // Check if button is visible and clickable
                const rect = button.getBoundingClientRect();
                const computedStyle = getComputedStyle(button);
                const isVisible = rect.width > 0 && rect.height > 0 && 
                                computedStyle.display !== 'none' && 
                                computedStyle.visibility !== 'hidden' &&
                                computedStyle.opacity !== '0';
                
                if (isVisible) {
                  const ariaLabel = button.getAttribute('aria-label');
                  const textContent = button.textContent?.trim();
                  
                  (window as any).logBot?.(`✅ Found clickable button: aria-label="${ariaLabel}", text="${textContent}"`);
                  
                  // Scroll into view and click
                  button.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  await new Promise((resolve) => setTimeout(resolve, 500));
                  
                  (window as any).logBot?.(`🖱️ Clicking Google Meet button...`);
                  button.click();
                  await new Promise((resolve) => setTimeout(resolve, 1000));
                  
                  (window as any).logBot?.(`✅ Successfully clicked button with selector: ${selector}`);
                  return true;
                } else {
                  (window as any).logBot?.(`ℹ️ Button found but not visible for selector: ${selector}`);
                }
              } else {
                (window as any).logBot?.(`ℹ️ No button found for selector: ${selector}`);
              }
            } catch (e: any) {
              (window as any).logBot?.(`❌ Error with selector ${selector}: ${e.message}`);
              continue;
            }
          }
          
          (window as any).logBot?.("❌ No working leave/cancel button found - tried all selectors");
          return false;
        } catch (err: any) {
          (window as any).logBot?.(`Error during Google Meet leave attempt: ${err.message}`);
          return false;
        }
      };
    }
  }, { googleLeaveSelectors });
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveGoogleMeet(page: Page | null, botConfig?: BotConfig, reason: string = "manual_leave"): Promise<boolean> {
  log("[leaveGoogleMeet] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveGoogleMeet] Page is not available or closed.");
    return false;
  }

  // Flush browser-side recording blob before UI leave and process shutdown.
  // This ensures Google Meet audio is persisted before upload is attempted.
  try {
    log("[leaveGoogleMeet] Flushing browser recording blob before leave...");
    await page.evaluate(async () => {
      const flushFn = (window as any).__vexaFlushRecordingBlob;
      if (typeof flushFn === "function") {
        await flushFn("manual_leave");
      }
    });
  } catch (flushError: any) {
    log(`[leaveGoogleMeet] Recording flush failed: ${flushError.message}`);
  }

  // Call leave callback first to notify bot-manager
  if (botConfig) {
    try {
      log("🔥 Calling leave callback before attempting to leave...");
      await callLeaveCallback(botConfig, reason);
      log("✅ Leave callback sent successfully");
    } catch (callbackError: any) {
      log(`⚠️ Warning: Failed to send leave callback: ${callbackError.message}. Continuing with leave attempt...`);
    }
  } else {
    log("⚠️ Warning: No bot config provided, cannot send leave callback");
  }

  try {
    const result = await page.evaluate(async () => {
      if (typeof (window as any).performLeaveAction === "function") {
        return await (window as any).performLeaveAction();
      } else {
        (window as any).logBot?.("[Node Eval Error] performLeaveAction function not found on window.");
        console.error("[Node Eval Error] performLeaveAction function not found on window.");
        return false;
      }
    });
    log(`[leaveGoogleMeet] Browser leave action result: ${result}`);
    return result;
  } catch (error: any) {
    log(`[leaveGoogleMeet] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}
