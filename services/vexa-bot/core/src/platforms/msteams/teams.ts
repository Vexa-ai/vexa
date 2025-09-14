import { Page } from "playwright";
import { log, randomDelay, callStartupCallback } from "../../utils";
import { BotConfig } from "../../types";
import { generateUUID, createSessionControlMessage, createSpeakerActivityMessage } from "../../index";
import { WhisperLiveService } from "../../services/whisperlive";
import { AudioService } from "../../services/audio";
import { WebSocketManager } from "../../utils/websocket";


// --- Teams-Specific Functions ---

// New function to wait for Teams meeting admission
const waitForTeamsMeetingAdmission = async (
  page: Page,
  timeout: number
): Promise<boolean> => {
  try {
    log("Waiting for Teams meeting admission...");
    
    // Take screenshot at start of admission check
    await page.screenshot({ path: '/app/screenshots/teams-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Start of Teams admission check");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the Teams meeting...");
    const initialAdmissionIndicators = [
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Chat"]', 
      'button[aria-label*="chat"]',
      'button[aria-label*="Leave"]',
      'button[aria-label*="leave"]',
      'button[aria-label*="End meeting"]',
      'button[aria-label*="end meeting"]',
      '[role="toolbar"]',
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]',
      'button[aria-label*="Mute"]',
      'button[aria-label*="mute"]',
      'button[aria-label*="Camera"]',
      'button[aria-label*="camera"]'
    ];
    
    for (const indicator of initialAdmissionIndicators) {
      try {
        await page.waitForSelector(indicator, { timeout: 2000 });
        log(`Found Teams admission indicator: ${indicator} - Bot is already admitted to the meeting!`);
        
        // Take screenshot when already admitted
        await page.screenshot({ path: '/app/screenshots/teams-admitted.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed already admitted to Teams meeting");
        
        log("Successfully admitted to the Teams meeting - no waiting room required");
        return true;
      } catch {
        continue;
      }
    }
    
    log("Bot not yet admitted - checking for Teams waiting room indicators...");
    
    // Second, check if we're still in waiting room
    const waitingRoomIndicators = [
      'text="You\'re in the lobby"',
      'text="Waiting for someone to let you in"',
      'text="Please wait until someone admits you"',
      'text="Wait for someone to admit you"',
      'text="Waiting to be admitted"',
      '[aria-label*="waiting"]',
      '[aria-label*="lobby"]',
      'text="Your request to join has been sent"',
      'text="Meeting not found"'
    ];
    
    // Check for waiting room indicators first, but don't exit immediately
    let stillInWaitingRoom = false;
    for (const waitingIndicator of waitingRoomIndicators) {
      try {
        await page.waitForSelector(waitingIndicator, { timeout: 2000 });
        log(`Found Teams waiting room indicator: ${waitingIndicator} - Bot is still in waiting room`);
        
        // Take screenshot when waiting room indicator found
        await page.screenshot({ path: '/app/screenshots/teams-waiting-room.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed in Teams waiting room");
        
        stillInWaitingRoom = true;
        break;
      } catch {
        // Continue to next indicator if this one wasn't found
        continue;
      }
    }
    
    // If we're in waiting room, wait for the full timeout period for admission
    if (stillInWaitingRoom) {
      log(`Bot is in Teams waiting room. Waiting for ${timeout}ms for admission...`);
      
      // Wait for the full timeout period, checking periodically for admission
      const checkInterval = 5000; // Check every 5 seconds
      const startTime = Date.now();
      let screenshotCounter = 0;
      
      while (Date.now() - startTime < timeout) {
        // Take periodic screenshot for debugging
        screenshotCounter++;
        await page.screenshot({ path: `/app/screenshots/teams-waiting-periodic-${screenshotCounter}.png`, fullPage: true });
        log(`📸 Periodic screenshot ${screenshotCounter} taken during Teams waiting period`);
        
        // Check if we're still in waiting room
        let stillWaiting = false;
        for (const waitingIndicator of waitingRoomIndicators) {
          try {
            await page.waitForSelector(waitingIndicator, { timeout: 1000 });
            stillWaiting = true;
            break;
          } catch {
            continue;
          }
        }
        
        if (!stillWaiting) {
          log("Teams waiting room indicator disappeared - bot is likely admitted, checking for admission indicators...");
          
          // Immediately check for admission indicators since waiting room disappeared
          let quickAdmissionCheck = false;
          for (const indicator of initialAdmissionIndicators) {
            try {
              await page.waitForSelector(indicator, { timeout: 1000 });
              log(`Found Teams admission indicator: ${indicator} - Bot is confirmed admitted!`);
              quickAdmissionCheck = true;
              break;
            } catch {
              continue;
            }
          }
          
          if (quickAdmissionCheck) {
            log("Successfully admitted to the Teams meeting - waiting room disappeared and admission indicators found");
            return true;
          } else {
            log("Teams waiting room disappeared but no admission indicators found yet - continuing to wait...");
            // Continue waiting for admission indicators to appear
          }
        }
        
        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in Teams waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room
      let finalWaitingCheck = false;
      for (const waitingIndicator of waitingRoomIndicators) {
        try {
          await page.waitForSelector(waitingIndicator, { timeout: 2000 });
          finalWaitingCheck = true;
          break;
        } catch {
          continue;
        }
      }
      
      if (finalWaitingCheck) {
        throw new Error("Bot is still in the Teams waiting room after timeout - not admitted to the meeting");
      }
    }
    
    // PRIORITY: Check for Teams meeting controls/toolbar (most reliable indicator)
    log("Checking for Teams meeting controls as primary admission indicator...");
    
    // Take initial screenshot before checking for admission indicators
    await page.screenshot({ path: '/app/screenshots/teams-checking-admission-start.png', fullPage: true });
    log("📸 Screenshot taken: Starting Teams admission indicator check");
    
    // Wait for Teams meeting indicators - these are the most reliable signs of admission
    // ORDERED BY LIKELIHOOD: Most common indicators first for faster detection
    const teamsAdmissionIndicators = [
      // Most common Teams meeting indicators (check these first!)
      'button[aria-label*="Chat"]',
      'button[aria-label*="chat"]', 
      'button[aria-label*="People"]',
      'button[aria-label*="people"]',
      'button[aria-label*="Participants"]',
      'button[aria-label*="Leave"]',
      'button[aria-label*="leave"]',
      // Audio/video controls that appear when in Teams meeting
      'button[aria-label*="Turn off microphone"]',
      'button[aria-label*="Turn on microphone"]',
      'button[aria-label*="Mute"]',
      'button[aria-label*="mute"]',
      'button[aria-label*="Turn off camera"]',
      'button[aria-label*="Turn on camera"]',
      'button[aria-label*="Camera"]',
      'button[aria-label*="camera"]',
      // Share and present buttons
      'button[aria-label*="Share"]',
      'button[aria-label*="share"]',
      'button[aria-label*="Present"]',
      'button[aria-label*="present"]',
      // Meeting toolbar and controls
      '[role="toolbar"]',
      // Teams specific meeting UI
      '[data-tid*="meeting"]',
      '[data-tid*="call"]'
    ];
    
    let admitted = false;
    let admissionCheckCounter = 0;
    
    // Use much faster timeout for each check (500ms instead of timeout/indicators.length)
    const fastTimeout = 500;
    
    for (const indicator of teamsAdmissionIndicators) {
      try {
        admissionCheckCounter++;
        log(`Checking Teams admission indicator ${admissionCheckCounter}/${teamsAdmissionIndicators.length}: ${indicator}`);
        
        // Take screenshot before checking each indicator
        await page.screenshot({ path: `/app/screenshots/teams-admission-check-${admissionCheckCounter}.png`, fullPage: true });
        log(`📸 Screenshot taken: Checking Teams admission indicator ${admissionCheckCounter}`);
        
        await page.waitForSelector(indicator, { timeout: fastTimeout });
        log(`Found Teams admission indicator: ${indicator} - Bot is admitted to the meeting`);
        
        // Take screenshot when admission indicator is found
        await page.screenshot({ path: '/app/screenshots/teams-admitted-confirmed.png', fullPage: true });
        log("📸 Screenshot taken: Bot confirmed admitted to Teams meeting via admission indicators");
        
        admitted = true;
        break;
      } catch {
        // Continue to next indicator
        continue;
      }
    }
    
    if (!admitted) {
      // Take screenshot when no meeting indicators found
      await page.screenshot({ path: '/app/screenshots/teams-no-indicators.png', fullPage: true });
      log("📸 Screenshot taken: No Teams meeting indicators found");
      
      // If we can't find any meeting indicators, the bot likely failed to join
      log("No Teams meeting indicators found - bot likely failed to join or is in unknown state");
      throw new Error("Bot failed to join the Teams meeting - no meeting indicators found");
    }
    
    if (admitted) {
      log("Successfully admitted to the Teams meeting");
      return true;
    } else {
      throw new Error("Could not determine Teams admission status");
    }
    
  } catch (error: any) {
    throw new Error(
      `Bot was not admitted into the Teams meeting within the timeout period: ${error.message}`
    );
  }
};

// Modified to use new services - Teams recording functionality
const startTeamsRecording = async (page: Page, botConfig: BotConfig) => {
  // Initialize WhisperLive service on Node.js side
  const whisperLiveService = new WhisperLiveService({
    redisUrl: botConfig.redisUrl,
    maxClients: parseInt(process.env.WL_MAX_CLIENTS || '10', 10),
    whisperLiveUrl: process.env.WHISPER_LIVE_URL
  });

  // Initialize WhisperLive connection
  const whisperLiveUrl = await whisperLiveService.initialize();
  if (!whisperLiveUrl) {
    log("ERROR: Could not initialize WhisperLive service for Teams. Aborting recording.");
    return;
  }

  log(`[Node.js] Using WhisperLive URL for Teams: ${whisperLiveUrl}`);
  log("Starting Teams recording with WebSocket connection");

  // Load browser utility classes from the bundled global file
  await page.addScriptTag({
    path: require('path').join(__dirname, '../../browser-utils.global.js'),
  });

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      whisperUrlForBrowser: string;
    }) => {
      const { botConfigData, whisperUrlForBrowser } = pageArgs;

      // Use browser utility classes from the global bundle
      const { BrowserAudioService, BrowserWhisperLiveService } = (window as any).VexaBrowserUtils;
      
      const audioService = new BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      // Use BrowserWhisperLiveService with stubborn mode for Teams
      const whisperLiveService = new BrowserWhisperLiveService({
        whisperLiveUrl: whisperUrlForBrowser
      }, true); // Enable stubborn mode for Teams



      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Teams recording process with new services.");
          
          // Find and create combined audio stream
          audioService.findMediaElements().then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              reject(
                new Error(
                  "[Teams BOT Error] No active media elements found after multiple retries. Ensure the Teams meeting media is playing."
                )
              );
              return;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              reject(new Error("[Teams BOT Error] Failed to create combined audio stream"));
              return;
            }
            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            // Setup audio data processing
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, sessionStartTime: number | null) => {
              // (log trimmed)
              
              // Only send after server ready
              if (!whisperLiveService.isReady()) {
                // (log trimmed)
                return;
              }
              // Compute simple RMS and peak for diagnostics
              let sumSquares = 0;
              let peak = 0;
              for (let i = 0; i < audioData.length; i++) {
                const v = audioData[i];
                sumSquares += v * v;
                const a = Math.abs(v);
                if (a > peak) peak = a;
              }
              const rms = Math.sqrt(sumSquares / Math.max(1, audioData.length));
              // (log trimmed)
              // Diagnostic: send metadata first
              whisperLiveService.sendAudioChunkMetadata(audioData.length, 16000);
              // Send audio data to WhisperLive
              const success = whisperLiveService.sendAudioData(audioData);
              if (!success) {
                (window as any).logBot("Failed to send Teams audio data to WhisperLive");
              }
            });

            // Initialize WhisperLive WebSocket connection
            return await whisperLiveService.connectToWhisperLive(
              botConfigData,
              (data: any) => {
                // (log trimmed) received transcription message
                if (data["status"] === "ERROR") {
                  (window as any).logBot(`Teams WebSocket Server Error: ${data["message"]}`);
                } else if (data["status"] === "WAIT") {
                  (window as any).logBot(`Teams Server busy: ${data["message"]}`);
                } else if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                  whisperLiveService.setServerReady(true);
                  (window as any).logBot("Teams Server is ready.");
                } else if (data["language"]) {
                  (window as any).logBot(`Teams Language detected: ${data["language"]}`);
                } else if (data["message"] === "DISCONNECT") {
                  (window as any).logBot("Teams Server requested disconnect.");
                  whisperLiveService.close();
                } else {
                  // (log trimmed) transcription summary
                }
              },
              (event: Event) => {
                (window as any).logBot(`[Teams Failover] WebSocket error. This will trigger retry logic.`);
              },
              async (event: CloseEvent) => {
                (window as any).logBot(`[Teams Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}.`);
                // Retry logic would be handled by WebSocketManager
              }
            );
          }).then(() => {
            // Initialize Teams-specific speaker detection (browser context)
            (window as any).logBot("Initializing Teams speaker detection...");
            
            // Teams-specific speaker detection logic (comprehensive like Google Meet)
            const initializeTeamsSpeakerDetection = (whisperLiveService: any, audioService: any, botConfigData: any) => {
              (window as any).logBot("Setting up Teams speaker detection...");
              
              // Teams-specific configuration for speaker detection
              const participantSelectors = [
                '[data-tid="voice-level-stream-outline"]', // Main speaker indicator
                '[data-tid*="participant"]',
                '[aria-label*="participant"]',
                '[data-tid*="roster"]',
                '[data-tid*="roster-item"]',
                '[data-tid*="video-tile"]',
                '[data-tid*="videoTile"]',
                '[data-tid*="participant-tile"]',
                '[data-tid*="participantTile"]',
                '[role="listitem"]',
                '.participant-tile',
                '.video-tile',
                '.roster-item'
              ];
              
              // Teams-specific speaking/silence detection based on voice-level-stream-outline
              // The voice-level-stream-outline element appears/disappears or changes state when someone speaks
              const speakingIndicators = [
                '[data-tid="voice-level-stream-outline"]'
              ];
              
              // Teams-specific speaking/silence classes (fallback)
              const speakingClasses = [
                'speaking', 'active-speaker', 'speaker-active', 'speaking-indicator',
                'audio-active', 'mic-active', 'microphone-active', 'voice-active',
                'speaking-border', 'speaking-glow', 'speaking-highlight',
                'participant-speaking', 'user-speaking', 'speaker-indicator'
              ];
              
              const silenceClasses = [
                'silent', 'muted', 'mic-off', 'microphone-off', 'audio-inactive',
                'participant-silent', 'user-silent', 'no-audio'
              ];
              
              // State for tracking speaking status
              const speakingStates = new Map(); // Stores the logical speaking state for each participant ID
              const activeParticipants = new Map(); // Central map for all known participants
              
              // Helper functions for Teams speaker detection
              function getTeamsParticipantId(element: HTMLElement) {
                // Try various Teams-specific attributes
                let id = element.getAttribute('data-tid') || 
                        element.getAttribute('data-participant-id') ||
                        element.getAttribute('data-user-id') ||
                        element.getAttribute('data-object-id') ||
                        element.getAttribute('id');
                
                if (!id) {
                  // Look for stable child elements
                  const stableChild = element.querySelector('[data-tid], [data-participant-id], [data-user-id]');
                  if (stableChild) {
                    id = stableChild.getAttribute('data-tid') || 
                         stableChild.getAttribute('data-participant-id') ||
                         stableChild.getAttribute('data-user-id');
                  }
                }
                
                if (!id) {
                  // Generate a stable ID if none found
                  if (!(element as any).dataset.vexaGeneratedId) {
                    (element as any).dataset.vexaGeneratedId = 'teams-id-' + Math.random().toString(36).substr(2, 9);
                  }
                  id = (element as any).dataset.vexaGeneratedId;
                }
                
                return id;
              }
              
              function getTeamsParticipantName(participantElement: HTMLElement) {
                // Teams-specific name selectors based on actual UI structure
                const teamsNameSelectors = [
                  // Look for the actual name div structure
                  'div[class*="___2u340f0"]', // The actual name div class pattern
                  '[data-tid*="display-name"]',
                  '[data-tid*="participant-name"]',
                  '[data-tid*="user-name"]',
                  '[aria-label*="name"]',
                  '.participant-name',
                  '.display-name',
                  '.user-name',
                  '.roster-item-name',
                  '.video-tile-name',
                  'span[title]',
                  '[title*="name"]',
                  '.ms-Persona-primaryText',
                  '.ms-Persona-secondaryText'
                ];
                
                // Try to find name in the main element or its children
                for (const selector of teamsNameSelectors) {
                  const nameElement = participantElement.querySelector(selector) as HTMLElement;
                  if (nameElement) {
                    let nameText = nameElement.textContent || 
                                  nameElement.innerText || 
                                  nameElement.getAttribute('title') ||
                                  nameElement.getAttribute('aria-label');
                    
                    if (nameText && nameText.trim()) {
                      // Clean up the name text
                      nameText = nameText.trim();
                      
                      // Filter out non-name content
                      const forbiddenSubstrings = [
                        "more_vert", "mic_off", "mic", "videocam", "videocam_off", 
                        "present_to_all", "devices", "speaker", "speakers", "microphone",
                        "camera", "camera_off", "share", "chat", "participant", "user"
                      ];
                      
                      if (nameText && !forbiddenSubstrings.some(sub => nameText!.toLowerCase().includes(sub.toLowerCase()))) {
                        // Basic validation
                        if (nameText.length > 1 && nameText.length < 50 && /^[\p{L}\s.'-]+$/u.test(nameText)) {
                          return nameText;
                        }
                      }
                    }
                  }
                }
                
                // Fallback: try to extract from aria-label
                const ariaLabel = participantElement.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.includes('name')) {
                  const nameMatch = ariaLabel.match(/name[:\s]+([^,]+)/i);
                  if (nameMatch && nameMatch[1]) {
                    const nameText = nameMatch[1].trim();
                    if (nameText.length > 1 && nameText.length < 50) {
                      return nameText;
                    }
                  }
                }
                
                // Final fallback
                const idToDisplay = getTeamsParticipantId(participantElement);
                return `Teams Participant (${idToDisplay})`;
              }
              
              function sendTeamsSpeakerEvent(eventType: string, participantElement: HTMLElement) {
                const eventAbsoluteTimeMs = Date.now();
                        const sessionStartTime = audioService.getSessionAudioStartTime();
                
                if (sessionStartTime === null) {
                  // (log trimmed)
                  return;
                }
                
                const relativeTimestampMs = eventAbsoluteTimeMs - sessionStartTime;
                const participantId = getTeamsParticipantId(participantElement);
                const participantName = getTeamsParticipantName(participantElement);
                
                // Send via BrowserWhisperLiveService helper (handles OPEN state internally)
                try {
                  const sent = whisperLiveService.sendSpeakerEvent(
                    eventType,
                    participantName,
                    participantId,
                    relativeTimestampMs,
                    botConfigData
                  );
                  if (sent) {
                    // (log trimmed)
                } else {
                    // (log trimmed)
                  }
                } catch (error: any) {
                  // (log trimmed)
                }
              }
              
              function logTeamsSpeakerEvent(participantElement: HTMLElement, mutatedClassList: DOMTokenList) {
                const participantId = getTeamsParticipantId(participantElement);
                const participantName = getTeamsParticipantName(participantElement);
                const previousLogicalState = speakingStates.get(participantId) || "silent";
                
                // Check for voice-level-stream-outline element (primary Teams speaker indicator)
                // NOTE: voice-level-stream-outline appears when participant is SILENT, disappears when SPEAKING
                const voiceLevelElement = participantElement.querySelector('[data-tid="voice-level-stream-outline"]') as HTMLElement;
                const isVoiceLevelVisible = voiceLevelElement && 
                  voiceLevelElement.offsetWidth > 0 && 
                  voiceLevelElement.offsetHeight > 0 &&
                  getComputedStyle(voiceLevelElement).display !== 'none' &&
                  getComputedStyle(voiceLevelElement).visibility !== 'hidden';
                
                // Fallback to class-based detection
                const isNowVisiblySpeaking = speakingClasses.some(cls => mutatedClassList.contains(cls));
                const isNowVisiblySilent = silenceClasses.some(cls => mutatedClassList.contains(cls));
                
                // Determine if currently speaking based on voice-level-stream-outline visibility
                // Voice level visible = participant is SILENT, voice level hidden = participant is SPEAKING
                const isCurrentlySpeaking = !isVoiceLevelVisible || isNowVisiblySpeaking;
                
                if (isCurrentlySpeaking) {
                  if (previousLogicalState !== "speaking") {
                    (window as any).logBot(`🎤 [Teams] SPEAKER_START: ${participantName} (ID: ${participantId}) - Voice level visible: ${isVoiceLevelVisible}`);
                    sendTeamsSpeakerEvent("SPEAKER_START", participantElement);
                  }
                  speakingStates.set(participantId, "speaking");
                } else {
                  if (previousLogicalState === "speaking") {
                    (window as any).logBot(`🔇 [Teams] SPEAKER_END: ${participantName} (ID: ${participantId}) - Voice level visible: ${isVoiceLevelVisible}`);
                    sendTeamsSpeakerEvent("SPEAKER_END", participantElement);
                  }
                  speakingStates.set(participantId, "silent");
                }
              }
              
              function observeTeamsParticipant(participantElement: HTMLElement) {
                const participantId = getTeamsParticipantId(participantElement);
                const participantName = getTeamsParticipantName(participantElement);
                
                // Initialize participant as silent
                speakingStates.set(participantId, "silent");
                
                // Check initial state
                let classListForInitialScan = participantElement.classList;
                for (const cls of speakingClasses) {
                  const descendantElement = participantElement.querySelector('.' + cls);
                  if (descendantElement) {
                    classListForInitialScan = descendantElement.classList;
                    break;
                  }
                }
                
                (window as any).logBot(`👁️ [Teams] Observing: ${participantName} (ID: ${participantId}). Performing initial participant state analysis.`);
                
                // DEBUG: Log all current classes on the participant element
                const allClasses = Array.from(participantElement.classList);
                // (log trimmed)
                
                // Also check child elements for classes
                const childElements = participantElement.querySelectorAll('*');
                childElements.forEach((child, index) => {
                  if (child.classList.length > 0) {
                    const childClasses = Array.from(child.classList);
                    // (log trimmed)
                  }
                });
                
                logTeamsSpeakerEvent(participantElement, classListForInitialScan);
                
                // Add participant to central map
                activeParticipants.set(participantId, { name: participantName, element: participantElement });
                
                const callback = function(mutationsList: MutationRecord[], observer: MutationObserver) {
                  for (const mutation of mutationsList) {
                    if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                      const targetElement = mutation.target as HTMLElement;
                      if (participantElement.contains(targetElement)) {
                        // DEBUG: Log class changes
                        const newClasses = Array.from(targetElement.classList);
                        // (log trimmed)
                        logTeamsSpeakerEvent(participantElement, targetElement.classList);
                      }
                    }
                  }
                };
                
                const observer = new MutationObserver(callback);
                observer.observe(participantElement, { 
                  attributes: true, 
                  attributeFilter: ['class'],
                  subtree: true 
                });
                
                if (!(participantElement as any).dataset.vexaObserverAttached) {
                  (participantElement as any).dataset.vexaObserverAttached = 'true';
                }
              }
              
              function scanForAllTeamsParticipants() {
                for (const selector of participantSelectors) {
                  const participantElements = document.querySelectorAll(selector);
                  for (let i = 0; i < participantElements.length; i++) {
                    const el = participantElements[i] as HTMLElement;
                    if (!(el as any).dataset.vexaObserverAttached) {
                      observeTeamsParticipant(el);
                    }
                  }
                }
              }
              
              // Initialize speaker detection
              scanForAllTeamsParticipants();
              
              // Monitor for new participants
              const bodyObserver = new MutationObserver((mutationsList) => {
                for (const mutation of mutationsList) {
                  if (mutation.type === 'childList') {
                    mutation.addedNodes.forEach(node => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        
                        // Check if the added node matches any participant selector
                        for (const selector of participantSelectors) {
                          if (elementNode.matches(selector) && !(elementNode as any).dataset.vexaObserverAttached) {
                            observeTeamsParticipant(elementNode);
                          }
                          
                          // Check children
                          const childElements = elementNode.querySelectorAll(selector);
                          for (let i = 0; i < childElements.length; i++) {
                            const childEl = childElements[i] as HTMLElement;
                            if (!(childEl as any).dataset.vexaObserverAttached) {
                              observeTeamsParticipant(childEl);
                            }
                          }
                        }
                      }
                    });
                    
                    mutation.removedNodes.forEach(node => {
                      if (node.nodeType === Node.ELEMENT_NODE) {
                        const elementNode = node as HTMLElement;
                        
                        // Check if removed node was a participant
                        for (const selector of participantSelectors) {
                          if (elementNode.matches(selector)) {
                            const participantId = getTeamsParticipantId(elementNode);
                            const participantName = getTeamsParticipantName(elementNode);
                            
                            if (speakingStates.get(participantId) === 'speaking') {
                              (window as any).logBot(`🔇 [Teams] SPEAKER_END (Participant removed while speaking): ${participantName} (ID: ${participantId})`);
                              sendTeamsSpeakerEvent("SPEAKER_END", elementNode);
                            }
                            
                            speakingStates.delete(participantId);
                            activeParticipants.delete(participantId);
                            delete (elementNode as any).dataset.vexaObserverAttached;
                            delete (elementNode as any).dataset.vexaGeneratedId;
                            (window as any).logBot(`🗑️ [Teams] Removed observer for: ${participantName} (ID: ${participantId})`);
                          }
                        }
                      }
                    });
                  }
                }
              });

              // Start observing the Teams meeting container
              const meetingContainer = document.querySelector('[role="main"]') || document.body;
              bodyObserver.observe(meetingContainer, {
                childList: true,
                subtree: true
              });

              // Expose active participants count for meeting monitoring
              (window as any).getTeamsActiveParticipantsCount = () => activeParticipants.size;
              (window as any).getTeamsActiveParticipants = () => Array.from(activeParticipants.keys());
              
              // Debug helpers removed to reduce log noise

              // Fallback: polling-based detection tailored for MS Teams
              // Periodically scan participant containers and detect speaking based on visibility of voice-level outline
              const teamsParticipantContainerSelectors = [
                '[data-tid*="participant"]',
                '[data-tid*="roster-item"]',
                '[data-tid*="video-tile"]',
                '[data-tid*="videoTile"]',
                '.participant-tile',
                '.video-tile'
              ];

              const lastSpeakingStateById = new Map();
              const POLL_MS = 500;

              const isVoiceLevelVisibleForContainer = (containerEl: HTMLElement): boolean => {
                // Primary Teams indicator
                // NOTE: voice-level-stream-outline appears when participant is SILENT, disappears when SPEAKING
                const voiceLevel = containerEl.querySelector('[data-tid="voice-level-stream-outline"]') as HTMLElement | null;
                const visible = (el: HTMLElement) => {
                  const cs = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                  const transform = cs.transform || '';
                  const scaledToZero = /matrix\((?:[^,]+,){4}\s*0(?:,|\s*\))/.test(transform) || transform.includes('scale(0');
                  const occluded = !!el.closest('.vdi-frame-occlusion');
                  return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    cs.display !== 'none' &&
                    cs.visibility !== 'hidden' &&
                    cs.opacity !== '0' &&
                    !ariaHidden &&
                    !scaledToZero &&
                    !occluded
                  );
                };

                // Voice level visible = participant is SILENT, voice level hidden = participant is SPEAKING
                if (voiceLevel && visible(voiceLevel)) return false; // Return false for speaking (voice level visible means silent)

                // Fallbacks: any child with class patterns suggesting audio activity
                const fallback = containerEl.querySelector(
                  '[class*="voice" i][class*="level" i], [class*="speaking" i], [data-audio-active="true"]'
                ) as HTMLElement | null;
                if (fallback && visible(fallback)) return true; // Return true for speaking (fallback indicators)

                return true; // Default to speaking if no voice level indicator found
              };

              const pollTeamsActiveSpeakers = () => {
                try {
                  const containers: HTMLElement[] = [];
                  teamsParticipantContainerSelectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach((el) => {
                      containers.push(el as HTMLElement);
                    });
                  });

                  containers.forEach((container) => {
                    const idRaw = getTeamsParticipantId(container) as any;
                    const nameRaw = getTeamsParticipantName(container) as any;
                    const participantName = String(nameRaw || 'Unknown Participant');
                    const participantId = String(idRaw || participantName);
                    const speaking = isVoiceLevelVisibleForContainer(container);
                    const prev = lastSpeakingStateById.get(participantId) || 'silent';

                    if (speaking && prev !== 'speaking') {
                      const ts = new Date().toISOString();
                      (window as any).logBot(`[${ts}] [SPEAKER_START] ${participantName}`);
                      sendTeamsSpeakerEvent('SPEAKER_START', container);
                      lastSpeakingStateById.set(participantId, 'speaking');
                    } else if (!speaking && prev === 'speaking') {
                      const ts = new Date().toISOString();
                      (window as any).logBot(`[${ts}] [SPEAKER_END] ${participantName}`);
                      sendTeamsSpeakerEvent('SPEAKER_END', container);
                      lastSpeakingStateById.set(participantId, 'silent');
                    } else if (!lastSpeakingStateById.has(participantId)) {
                      lastSpeakingStateById.set(participantId, speaking ? 'speaking' : 'silent');
                    }
                  });
                } catch (e: any) {
                  // (log trimmed)
                }
              };

              // Start polling loop (container-based visibility)
              setInterval(pollTeamsActiveSpeakers, POLL_MS);

              // Teams-specific: Poll explicit voice-level indicators and emit START/END on presence changes
              const lastIndicatorStateById = new Map<string, boolean>();
              const lastEventTsById = new Map<string, number>();
              const lastSeenTsById = new Map<string, number>();
              const observedIndicators = new WeakSet<HTMLElement>();
              const DEBOUNCE_MS = 300; // reduce duplicate START spam
              const INACTIVITY_MS = 2000; // END after no visible indicator for this long

              const getContainerForIndicator = (indicator: HTMLElement): HTMLElement | null => {
                // Prefer explicit container if present
                const container = indicator.closest('[data-stream-type]') as HTMLElement | null;
                if (container) return container;
                // Fallback to a few parent hops
                let parent: HTMLElement | null = indicator.parentElement;
                let hops = 0;
                while (parent && hops < 5) {
                  if (parent.hasAttribute('data-tid') || parent.hasAttribute('data-stream-type')) return parent;
                  parent = parent.parentElement;
                  hops++;
                }
                return indicator.parentElement as HTMLElement | null;
              };

              const pollTeamsVoiceIndicators = () => {
                try {
                  // Collect indicators across same-origin iframes as well
                  const getAllDocuments = (): Document[] => {
                    const docs: Document[] = [document];
                    const visit = (doc: Document) => {
                      const iframes = Array.from(doc.querySelectorAll('iframe')) as HTMLIFrameElement[];
                      for (const frame of iframes) {
                        try {
                          const fd = frame.contentDocument;
                          if (fd && fd.domain === document.domain) {
                            docs.push(fd);
                            visit(fd);
                          }
                        } catch (_) { /* cross-origin, ignore */ }
                      }
                    };
                    visit(document);
                    return docs;
                  };

                  const allDocs = getAllDocuments();
                  const indicators: HTMLElement[] = [];
                  for (const doc of allDocs) {
                    doc.querySelectorAll('[data-tid="voice-level-stream-outline"]').forEach(el => indicators.push(el as HTMLElement));
                  }
                  const currentSpeakingIds = new Set<string>();

                  const isVisible = (el: HTMLElement) => {
                    const cs = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                    const transform = cs.transform || '';
                    const scaledToZero = /matrix\((?:[^,]+,){4}\s*0(?:,|\s*\))/.test(transform) || transform.includes('scale(0');
                    const occluded = !!el.closest('.vdi-frame-occlusion');
                    return (
                      rect.width > 0 &&
                      rect.height > 0 &&
                      cs.visibility !== 'hidden' &&
                      cs.display !== 'none' &&
                      cs.opacity !== '0' &&
                      !ariaHidden &&
                      !scaledToZero &&
                      !occluded
                    );
                  };

                  indicators.forEach((indicator) => {
                    const container = getContainerForIndicator(indicator);
                    if (!container) return;
                    // Try Teams-specific name div first
                    const nameDiv = container.querySelector('div[class*="___2u340f0"]') as HTMLElement | null;
                    const participantNameFromDiv = nameDiv && nameDiv.textContent ? nameDiv.textContent.trim() : null;
                    const participantIdRaw = getTeamsParticipantId(container) as unknown as string | null;
                    const participantNameRaw = participantNameFromDiv || (getTeamsParticipantName(container) as unknown as string | null);
                    const participantName = (participantNameRaw ?? 'Unknown Participant');
                    const participantId = (participantIdRaw ?? participantName);

                    // Track last seen for fallback END logic
                    lastSeenTsById.set(participantId, Date.now());

                    // Observe this indicator for visibility changes to emit END quickly
                    if (!observedIndicators.has(indicator)) {
                      try {
                        const observer = new MutationObserver(() => {
                          const currentlyVisible = isVisible(indicator);
                          const wasSpeaking = lastIndicatorStateById.get(participantId) === true;
                          // Voice level visible = participant is SILENT, voice level hidden = participant is SPEAKING
                          if (!currentlyVisible && !wasSpeaking) {
                            const ts = new Date().toISOString();
                            (window as any).logBot(`[${ts}] [SPEAKER_START] ${participantName}`);
                            sendTeamsSpeakerEvent('SPEAKER_START', container);
                            lastIndicatorStateById.set(participantId, true);
                            lastEventTsById.set(participantId, Date.now());
                          } else if (currentlyVisible && wasSpeaking) {
                            const ts = new Date().toISOString();
                            (window as any).logBot(`[${ts}] [SPEAKER_END] ${participantName}`);
                            sendTeamsSpeakerEvent('SPEAKER_END', container);
                            lastIndicatorStateById.set(participantId, false);
                            lastEventTsById.set(participantId, Date.now());
                          }
                        });
                        observer.observe(indicator, { attributes: true, attributeFilter: ['class', 'style', 'aria-hidden'] });
                        observedIndicators.add(indicator);
                      } catch {}
                    }

                    // Voice level visible = participant is SILENT, voice level hidden = participant is SPEAKING
                    if (!isVisible(indicator)) {
                      currentSpeakingIds.add(participantId);

                      const prevSpeaking = lastIndicatorStateById.get(participantId) === true;
                      const now = Date.now();
                      const lastTs = lastEventTsById.get(participantId) || 0;
                      if (!prevSpeaking && (now - lastTs) > DEBOUNCE_MS) {
                        const ts = new Date().toISOString();
                        (window as any).logBot(`[${ts}] [SPEAKER_START] ${participantName}`);
                        sendTeamsSpeakerEvent('SPEAKER_START', container);
                        lastIndicatorStateById.set(participantId, true);
                        lastEventTsById.set(participantId, now);
                      }
                    }
                  });

                  // Handle speakers that stopped (previously true but not in current set)
                  const nowTs = Date.now();
                  Array.from(lastIndicatorStateById.keys()).forEach((participantId) => {
                    const wasSpeaking = lastIndicatorStateById.get(participantId) === true;
                    const seenTs = lastSeenTsById.get(participantId) || 0;
                    if (wasSpeaking && !currentSpeakingIds.has(participantId) && (nowTs - seenTs) > INACTIVITY_MS) {
                      const participant = activeParticipants.get(participantId);
                      const element = participant?.element as HTMLElement | undefined;
                      const name = participant?.name || `Teams Participant (${participantId})`;
                      if (element) {
                        const ts = new Date().toISOString();
                        (window as any).logBot(`[${ts}] [SPEAKER_END] ${name}`);
                        sendTeamsSpeakerEvent('SPEAKER_END', element);
                      }
                      lastIndicatorStateById.set(participantId, false);
                      lastEventTsById.set(participantId, nowTs);
                    }
                  });
                } catch (e: any) {
                  // (log trimmed)
                }
              };

              // Poll indicators at 300ms for snappier detection
              setInterval(pollTeamsVoiceIndicators, 300);
            };

            // Setup Teams meeting monitoring (browser context)
            const setupTeamsMeetingMonitoring = (botConfigData: any, audioService: any, whisperLiveService: any, resolve: any) => {
              (window as any).logBot("Setting up Teams meeting monitoring...");
              
              const startupAloneTimeoutSeconds = 20 * 60; // 20 minutes during startup
              const everyoneLeftTimeoutSeconds = 10; // 10 seconds after speakers identified
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;

              const checkInterval = setInterval(() => {
                // Check participant count using the comprehensive speaker detection system
                const currentParticipantCount = (window as any).getTeamsActiveParticipantsCount ? (window as any).getTeamsActiveParticipantsCount() : 0;
                
                if (currentParticipantCount !== lastParticipantCount) {
                  // (log trimmed)
                  lastParticipantCount = currentParticipantCount;
                  
                  // Track if we've ever had multiple participants
                  if (currentParticipantCount > 1) {
                    hasEverHadMultipleParticipants = true;
                    speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                    (window as any).logBot("Teams Speakers identified - switching to post-speaker monitoring mode");
                  }
                }

                if (currentParticipantCount <= 1) {
                  aloneTime++;
                  
                  // Determine timeout based on whether speakers have been identified
                  const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                  const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                  
                  if (aloneTime >= currentTimeout) {
                    if (speakersIdentified) {
                      (window as any).logBot(`Teams meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                    } else {
                      (window as any).logBot(`Teams bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                    }
                    clearInterval(checkInterval);
                    audioService.disconnect();
                    whisperLiveService.close();
                    resolve();
                  } else if (aloneTime > 0 && aloneTime % 10 === 0) { // Log every 10 seconds to avoid spam
                    if (speakersIdentified) {
                      (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                    } else {
                      const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                      const remainingSeconds = (currentTimeout - aloneTime) % 60;
                      (window as any).logBot(`Teams bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                    }
                  }
                } else {
                  aloneTime = 0; // Reset if others are present
                  if (hasEverHadMultipleParticipants && !speakersIdentified) {
                    speakersIdentified = true;
                    (window as any).logBot("Teams speakers identified - switching to post-speaker monitoring mode");
                  }
                }
              }, 1000);

              // Listen for page unload
              window.addEventListener("beforeunload", () => {
                (window as any).logBot("Teams page is unloading. Stopping recorder...");
                clearInterval(checkInterval);
                audioService.disconnect();
                whisperLiveService.close();
                resolve();
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Teams document is hidden. Stopping recorder...");
                  clearInterval(checkInterval);
                  audioService.disconnect();
                  whisperLiveService.close();
                  resolve();
                }
              });
            };

            // Initialize Teams-specific speaker detection
            initializeTeamsSpeakerDetection(whisperLiveService, audioService, botConfigData);
            
            // Setup Teams meeting monitoring
            setupTeamsMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
          }).catch((err: any) => {
            reject(err);
          });

        } catch (error: any) {
          return reject(new Error("[Teams BOT Error] " + error.message));
        }
      });
    },
    { botConfigData: botConfig, whisperUrlForBrowser: whisperLiveUrl }
  );
  
  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
};

// Prepare for recording by exposing necessary functions
const prepareForRecording = async (page: Page): Promise<void> => {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });


  // Ensure leave function is available even before admission
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          // Teams-specific leave button selectors
          const primaryLeaveButtonSelectors = [
            'button[aria-label*="Leave"]',
            'button[aria-label*="leave"]', 
            'button[aria-label*="End meeting"]',
            'button[aria-label*="end meeting"]',
            'button[aria-label*="Hang up"]',
            'button[aria-label*="hang up"]'
          ];

          const secondaryLeaveButtonSelectors = [
            'button:has-text("Leave meeting")',
            'button:has-text("Leave")',
            'button:has-text("End meeting")',
            'button:has-text("Hang up")'
          ];

          // Try primary leave buttons
          for (const selector of primaryLeaveButtonSelectors) {
            try {
              const leaveButton = document.querySelector(selector) as HTMLElement;
              if (leaveButton) {
                (window as any).logBot?.(`Clicking Teams primary leave button: ${selector}`);
                leaveButton.click();
                await new Promise((resolve) => setTimeout(resolve, 1000));
                
                // Try secondary/confirmation buttons
                for (const secondarySelector of secondaryLeaveButtonSelectors) {
                  try {
                    const confirmButton = document.querySelector(secondarySelector) as HTMLElement;
                    if (confirmButton) {
                      (window as any).logBot?.(`Clicking Teams confirmation leave button: ${secondarySelector}`);
                      confirmButton.click();
                      await new Promise((resolve) => setTimeout(resolve, 500));
                      break;
                    }
                  } catch (e) {
                    continue;
                  }
                }
                
                (window as any).logBot?.("Teams leave sequence completed.");
                return true;
              }
            } catch (e) {
              continue;
            }
          }
          
          (window as any).logBot?.("Teams primary leave button not found.");
          return false;
        } catch (err: any) {
          (window as any).logBot?.(`Error during Teams leave attempt: ${err.message}`);
          return false;
        }
      };
    }
  });
};

export async function handleMicrosoftTeams(
  botConfig: BotConfig,
  page: Page,
  gracefulLeaveFunction: (page: Page | null, exitCode: number, reason: string, errorDetails?: any) => Promise<void>
): Promise<void> {
  log("Starting Microsoft Teams bot - Using simple approach with MS Edge");
  
  if (!botConfig.meetingUrl) {
    log("Error: Meeting URL is required for Microsoft Teams but is null.");
    await gracefulLeaveFunction(page, 1, "missing_meeting_url");
    return;
  }

  try {
    // Step 1: Navigate to Teams meeting (exactly like simple-bot.js)
    log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
    await page.goto(botConfig.meetingUrl, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(5000);
    
    // Take initial screenshot
    await page.screenshot({ path: '/app/screenshots/teams-step-1-initial.png', fullPage: true });
    log("📸 Screenshot: Step 1 - Initial page load");

    // Step 2: Try to find and click "Continue on this browser" button (exactly like simple-bot.js)
    log("Step 2: Looking for 'Continue on this browser' button...");
    try {
      const continueButton = page.getByRole('button', { name: 'Continue on this browser' });
      await continueButton.waitFor({ timeout: 10000 });
      await continueButton.click();
      log("✅ Clicked 'Continue on this browser'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Continue on this browser' button not found, trying alternative selectors...");
      
      // Try alternative selectors for continue button
      try {
        const altContinueButton = page.locator('button:has-text("Continue")').first();
        await altContinueButton.waitFor({ timeout: 5000 });
        await altContinueButton.click();
        log("✅ Clicked alternative continue button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative continue button not found, continuing...");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-2-after-continue.png', fullPage: true });
    log("📸 Screenshot: Step 2 - After continue click");

    // Step 3: Try to find and click "Join now" button (exactly like simple-bot.js)
    log("Step 3: Looking for 'Join now' button...");
    try {
      const joinNowButton = page.getByRole('button', { name: 'Join now' });
      await joinNowButton.waitFor({ timeout: 10000 });
      await joinNowButton.click();
      log("✅ Clicked 'Join now'");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ 'Join now' button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(3000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found, continuing...");
      }
    }

    // Step 4: Try to turn off camera (exactly like simple-bot.js)
    log("Step 4: Trying to turn off camera...");
    try {
      const cameraButton = page.getByRole('button', { name: 'Turn off camera' });
      await cameraButton.waitFor({ timeout: 5000 });
      await cameraButton.click();
      log("✅ Camera turned off");
    } catch (error) {
      log("ℹ️ Camera button not found or already off");
    }

    // Step 5: Try to set display name (exactly like simple-bot.js)
    log("Step 5: Trying to set display name...");
    try {
      const nameInput = page.getByRole('textbox', { name: 'Display name' });
      await nameInput.waitFor({ timeout: 5000 });
      await nameInput.fill(botConfig.botName);
      log(`✅ Display name set to "${botConfig.botName}"`);
    } catch (error) {
      log("ℹ️ Display name input not found, trying alternative selectors...");
      
      // Try alternative selectors for name input
      try {
        const altNameInput = page.locator('input[placeholder*="name"], input[placeholder*="Name"], input[type="text"]').first();
        await altNameInput.waitFor({ timeout: 5000 });
        await altNameInput.fill(botConfig.botName);
        log(`✅ Display name set to "${botConfig.botName}" using alternative selector`);
      } catch (altError) {
        log("ℹ️ Alternative name input not found, trying text-based selector...");
        
        // Try text-based selector
        try {
          const textNameInput = page.locator('input:has-text("Type your name")').first();
          await textNameInput.waitFor({ timeout: 3000 });
          await textNameInput.fill(botConfig.botName);
          log(`✅ Display name set to "${botConfig.botName}" using text selector`);
        } catch (textError) {
          log("ℹ️ Text-based name input not found, trying CSS selector...");
          
          // Try CSS selector for the name input
          try {
            const cssNameInput = page.locator('input[type="text"]').first();
            await cssNameInput.waitFor({ timeout: 3000 });
            await cssNameInput.fill(botConfig.botName);
            log(`✅ Display name set to "${botConfig.botName}" using CSS selector`);
          } catch (cssError) {
            log("ℹ️ CSS selector name input not found");
          }
        }
      }
    }

    // Step 6: Try to find final join button (exactly like simple-bot.js)
    log("Step 6: Looking for final join button...");
    try {
      const finalJoinButton = page.getByRole('button', { name: 'Join' });
      await finalJoinButton.waitFor({ timeout: 10000 });
      await finalJoinButton.click();
      log("✅ Clicked final join button");
    await page.waitForTimeout(5000);
    } catch (error) {
      log("ℹ️ Final join button not found, trying alternative selectors...");
      
      // Try alternative selectors for join button
      try {
        const altJoinButton = page.locator('button:has-text("Join now"), button:has-text("Join")').first();
        await altJoinButton.waitFor({ timeout: 5000 });
        await altJoinButton.click();
        log("✅ Clicked alternative join button");
        await page.waitForTimeout(5000);
      } catch (altError) {
        log("ℹ️ Alternative join button not found");
      }
    }

    await page.screenshot({ path: '/app/screenshots/teams-step-3-after-permissions.png', fullPage: true });
    log("📸 Screenshot: Step 3 - After join attempts");

    // Step 7: Check current state (exactly like simple-bot.js)
    log("Step 7: Checking current state...");
    const currentUrl = page.url();
    log(`📍 Current URL: ${currentUrl}`);
    
    // Setup websocket connection and meeting admission concurrently
    log("Starting WebSocket connection while waiting for Teams meeting admission");
    try {
      // Run both processes concurrently
      const [isAdmitted] = await Promise.all([
        // Wait for admission to the Teams meeting
        waitForTeamsMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout).catch((error) => {
          log("Teams meeting admission failed: " + error.message);
          return false;
        }),

        // Prepare for recording (expose functions, etc.) while waiting for admission
        prepareForRecording(page),
      ]);
    
    if (!isAdmitted) {
      log("Bot was not admitted into the Teams meeting within the timeout period. This is a normal completion.");
      await gracefulLeaveFunction(page, 0, "admission_failed");
      return;
    }

    log("Successfully admitted to the Teams meeting, starting recording");
    
    // --- ADDED: Call startup callback to notify bot-manager that bot is active ---
    try {
      await callStartupCallback(botConfig);
      log("Startup callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send startup callback: ${callbackError.message}. Continuing with recording...`);
    }
    
    // Start recording with Teams-specific logic
    await startTeamsRecording(page, botConfig);
    } catch (error: any) {
      console.error("Error after Teams join attempt (admission/recording setup): " + error.message);
      log("Error after Teams join attempt (admission/recording setup): " + error.message + ". Triggering graceful leave.");
      
      // Capture detailed error information for debugging
      const errorDetails = {
        error_message: error.message,
        error_stack: error.stack,
        error_name: error.name,
        context: "post_join_setup_error",
        platform: "teams",
        timestamp: new Date().toISOString()
      };
      
      // Use a general error code here, as it could be various issues.
      await gracefulLeaveFunction(page, 1, "post_join_setup_error", errorDetails);
      return;
    }

  } catch (error: any) {
    log(`❌ Error in Microsoft Teams bot: ${error.message}`);
    await gracefulLeaveFunction(page, 1, "teams_error", error);
  }
}

// --- ADDED: Exported function to trigger leave from Node.js ---
export async function leaveMicrosoftTeams(page: Page | null): Promise<boolean> {
  log("[leaveMicrosoftTeams] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveMicrosoftTeams] Page is not available or closed.");
    return false;
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
    log(`[leaveMicrosoftTeams] Browser leave action result: ${result}`);
    return result;
  } catch (error: any) {
    log(`[leaveMicrosoftTeams] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}