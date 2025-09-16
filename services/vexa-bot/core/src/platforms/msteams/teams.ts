import { Page } from "playwright";
import { log, randomDelay, callStartupCallback, callJoiningCallback, callAwaitingAdmissionCallback, callLeaveCallback } from "../../utils";
import { BotConfig } from "../../types";
import { generateUUID, createSessionControlMessage, createSpeakerActivityMessage } from "../../index";
import { WhisperLiveService } from "../../services/whisperlive";
import { AudioService } from "../../services/audio";
import { WebSocketManager } from "../../utils/websocket";
import { 
  teamsInitialAdmissionIndicators,
  teamsWaitingRoomIndicators,
  teamsAdmissionIndicators,
  teamsParticipantSelectors,
  teamsSpeakingClassNames,
  teamsSilenceClassNames,
  teamsParticipantContainerSelectors,
  teamsPrimaryLeaveButtonSelectors,
  teamsSecondaryLeaveButtonSelectors,
  teamsNameSelectors,
  teamsSpeakingIndicators,
  teamsRemovalIndicators,
  teamsContinueButtonSelectors,
  teamsJoinButtonSelectors,
  teamsCameraButtonSelectors,
  teamsNameInputSelectors,
  teamsMeetingContainerSelectors,
  teamsVoiceLevelSelectors,
  teamsOcclusionSelectors,
  teamsStreamTypeSelectors,
  teamsAudioActivitySelectors,
  teamsParticipantIdSelectors
} from "./selectors";


// --- Teams-Specific Functions ---

// Function to check if bot has been removed from the meeting
const checkForTeamsRemoval = async (page: Page): Promise<boolean> => {
  try {
    // Check for removal indicators
    for (const selector of teamsRemovalIndicators) {
      try {
        const element = await page.locator(selector).first();
        if (await element.isVisible()) {
          log(`🚨 Teams removal detected: Found removal indicator "${selector}"`);
          return true;
        }
      } catch (e) {
        // Continue checking other selectors
        continue;
      }
    }
    return false;
  } catch (error: any) {
    log(`Error checking for Teams removal: ${error.message}`);
    return false;
  }
};

// New function to wait for Teams meeting admission
const waitForTeamsMeetingAdmission = async (
  page: Page,
  timeout: number,
  botConfig: BotConfig
): Promise<boolean> => {
  try {
    log("Waiting for Teams meeting admission...");
    
    // FIRST: Check if bot is already admitted (no waiting room needed)
    log("Checking if bot is already admitted to the Teams meeting...");
    
    // Check for visible Leave button in meeting toolbar (single robust indicator)
    const initialLeaveButtonVisible = await page.locator(teamsInitialAdmissionIndicators[0]).first().isVisible();
    const initialLeaveButtonEnabled = initialLeaveButtonVisible && !(await page.locator(teamsInitialAdmissionIndicators[0]).first().getAttribute('aria-disabled'));
    
    // Negative check: ensure we're not still in lobby/pre-join
    const initialLobbyTextVisible = await page.locator(teamsWaitingRoomIndicators[0]).isVisible();
    const initialJoinNowButtonVisible = await page.getByRole('button', { name: /Join now/i }).isVisible();
    
    if (initialLeaveButtonVisible && initialLeaveButtonEnabled && !initialLobbyTextVisible && !initialJoinNowButtonVisible) {
      log(`Found Teams admission indicator: visible Leave button - Bot is already admitted to the meeting!`);
      
      // STATUS CHANGE: Bot is already admitted - take screenshot before AWAITING_ADMISSION callback
      await page.screenshot({ path: '/app/storage/screenshots/teams-status-awaiting-admission-immediate.png', fullPage: true });
      log("📸 Screenshot taken: Bot state when AWAITING_ADMISSION callback is triggered (immediate admission)");
      
      // --- Call awaiting admission callback even for immediate admission ---
      try {
        await callAwaitingAdmissionCallback(botConfig);
        log("Awaiting admission callback sent successfully (immediate admission)");
      } catch (callbackError: any) {
        log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}. Continuing...`);
      }
      
      log("Successfully admitted to the Teams meeting - no waiting room required");
      return true;
    }
    
    log("Bot not yet admitted - checking for Teams waiting room indicators...");
    
    // Check for waiting room indicators using visibility checks
    let stillInWaitingRoom = false;
    
    // Check for lobby text visibility
    const waitingLobbyTextVisible = await page.locator(teamsWaitingRoomIndicators[0]).isVisible();
    const waitingJoinNowButtonVisible = await page.getByRole('button', { name: /Join now/i }).isVisible();
    
    if (waitingLobbyTextVisible || waitingJoinNowButtonVisible) {
      log(`Found Teams waiting room indicator: lobby text or Join now button visible - Bot is still in waiting room`);
      
      // STATUS CHANGE: Bot is in waiting room - take screenshot before AWAITING_ADMISSION callback
      await page.screenshot({ path: '/app/storage/screenshots/teams-status-awaiting-admission-waiting-room.png', fullPage: true });
      log("📸 Screenshot taken: Bot state when AWAITING_ADMISSION callback is triggered (waiting room)");
      
      // --- Call awaiting admission callback to notify bot-manager that bot is waiting ---
      try {
        await callAwaitingAdmissionCallback(botConfig);
        log("Awaiting admission callback sent successfully");
      } catch (callbackError: any) {
        log(`Warning: Failed to send awaiting admission callback: ${callbackError.message}. Continuing with admission wait...`);
      }
      
      stillInWaitingRoom = true;
    }
    
    // If we're in waiting room, wait for the full timeout period for admission
    if (stillInWaitingRoom) {
      log(`Bot is in Teams waiting room. Waiting for ${timeout}ms for admission...`);
      
      // Wait for the full timeout period, checking periodically for admission (NO PERIODIC SCREENSHOTS)
      const checkInterval = 5000; // Check every 5 seconds
      const startTime = Date.now();
      
      while (Date.now() - startTime < timeout) {
        // Check if we're still in waiting room using visibility
        const lobbyTextStillVisible = await page.locator(teamsWaitingRoomIndicators[0]).isVisible();
        const joinNowButtonStillVisible = await page.getByRole('button', { name: /Join now/i }).isVisible();
        const stillWaiting = lobbyTextStillVisible || joinNowButtonStillVisible;
        
        if (!stillWaiting) {
          log("Teams waiting room indicator disappeared - bot is likely admitted, checking for admission indicators...");
          
          // Immediately check for admission indicators since waiting room disappeared
          const leaveButtonNowVisible = await page.locator(teamsInitialAdmissionIndicators[0]).first().isVisible();
          const leaveButtonNowEnabled = leaveButtonNowVisible && !(await page.locator(teamsInitialAdmissionIndicators[0]).first().getAttribute('aria-disabled'));
          
          if (leaveButtonNowVisible && leaveButtonNowEnabled) {
            log(`Found Teams admission indicator: visible Leave button - Bot is confirmed admitted!`);
            return true;
          } else {
            log("Teams waiting room disappeared - bot is admitted (no waiting room = admitted)");
            return true; // If waiting room disappeared, bot is admitted regardless of Leave button visibility
          }
        }
        
        // Wait before next check
        await page.waitForTimeout(checkInterval);
        log(`Still in Teams waiting room... ${Math.round((Date.now() - startTime) / 1000)}s elapsed`);
      }
      
      // After waiting, check if we're still in waiting room using visibility
      const finalLobbyTextVisible = await page.locator(teamsWaitingRoomIndicators[0]).isVisible();
      const finalJoinNowButtonVisible = await page.getByRole('button', { name: /Join now/i }).isVisible();
      const finalWaitingCheck = finalLobbyTextVisible || finalJoinNowButtonVisible;
      
      if (finalWaitingCheck) {
        throw new Error("Bot is still in the Teams waiting room after timeout - not admitted to the meeting");
      }
    }
    
    // PRIORITY: Check for Teams meeting controls/toolbar (most reliable indicator)
    log("Checking for Teams meeting controls as primary admission indicator...");
    
    // Check for visible Leave button in meeting toolbar (single robust indicator)
    log("Checking for visible Leave button in meeting toolbar...");
    
    const finalLeaveButtonVisible = await page.locator(teamsInitialAdmissionIndicators[0]).first().isVisible();
    const finalLeaveButtonEnabled = finalLeaveButtonVisible && !(await page.locator(teamsInitialAdmissionIndicators[0]).first().getAttribute('aria-disabled'));
    
    // Negative check: ensure we're not still in lobby/pre-join
    const finalLobbyTextVisible = await page.locator(teamsWaitingRoomIndicators[0]).isVisible();
    const finalJoinNowButtonVisible = await page.getByRole('button', { name: /Join now/i }).isVisible();
    
    const admitted = finalLeaveButtonVisible && finalLeaveButtonEnabled && !finalLobbyTextVisible && !finalJoinNowButtonVisible;
    
    if (admitted) {
      log(`Found Teams admission indicator: visible Leave button - Bot is admitted to the meeting`);
    }
    
    if (!admitted) {
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
      selectors: {
        participantSelectors: string[];
        speakingClasses: string[];
        silenceClasses: string[];
        containerSelectors: string[];
        nameSelectors: string[];
        speakingIndicators: string[];
      };
    }) => {
      const { botConfigData, whisperUrlForBrowser, selectors } = pageArgs;
      const selectorsTyped = selectors as any;

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
              const participantSelectors = selectors.participantSelectors;
              
              // Teams-specific speaking/silence detection based on voice-level-stream-outline
              // The voice-level-stream-outline element appears/disappears or changes state when someone speaks
              const speakingIndicators = selectors.speakingIndicators;
              
              // Teams-specific speaking/silence classes (fallback)
              const speakingClasses = selectors.speakingClasses;
              
              const silenceClasses = selectors.silenceClasses;
              
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
                  const stableChild = element.querySelector(selectorsTyped.participantIdSelectors.join(', '));
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
                const nameSelectors = selectors.nameSelectors;
                
                // Try to find name in the main element or its children
                for (const selector of nameSelectors) {
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
                const voiceLevelElement = participantElement.querySelector(selectorsTyped.voiceLevelSelectors[0]) as HTMLElement;
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
              const meetingContainer = document.querySelector(selectorsTyped.meetingContainerSelectors[0]) || document.body;
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
              const containerSelectors: string[] = selectors.containerSelectors;

              const lastSpeakingStateById = new Map();
              const POLL_MS = 500;

              const isVoiceLevelVisibleForContainer = (containerEl: HTMLElement): boolean => {
                // Primary Teams indicator
                // NOTE: voice-level-stream-outline appears when participant is SILENT, disappears when SPEAKING
                const voiceLevel = containerEl.querySelector(selectorsTyped.voiceLevelSelectors[0]) as HTMLElement | null;
                const visible = (el: HTMLElement) => {
                  const cs = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                  const transform = cs.transform || '';
                  const scaledToZero = /matrix\((?:[^,]+,){4}\s*0(?:,|\s*\))/.test(transform) || transform.includes('scale(0');
                  const occluded = !!el.closest(selectorsTyped.occlusionSelectors[0]);
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
                  selectorsTyped.audioActivitySelectors.join(', ')
                ) as HTMLElement | null;
                if (fallback && visible(fallback)) return true; // Return true for speaking (fallback indicators)

                return true; // Default to speaking if no voice level indicator found
              };

              const pollTeamsActiveSpeakers = () => {
                try {
                  const containers: HTMLElement[] = [];
                  containerSelectors.forEach(sel => {
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
                const container = indicator.closest(selectorsTyped.streamTypeSelectors[0]) as HTMLElement | null;
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
                    doc.querySelectorAll(selectorsTyped.voiceLevelSelectors[0]).forEach(el => indicators.push(el as HTMLElement));
                  }
                  const currentSpeakingIds = new Set<string>();

                  const isVisible = (el: HTMLElement) => {
                    const cs = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                    const transform = cs.transform || '';
                    const scaledToZero = /matrix\((?:[^,]+,){4}\s*0(?:,|\s*\))/.test(transform) || transform.includes('scale(0');
                    const occluded = !!el.closest(selectorsTyped.occlusionSelectors[0]);
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
                    const nameDiv = container.querySelector(selectors.nameSelectors[0]) as HTMLElement | null;
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

              // Teams removal detection function (browser context)
              const checkForRemoval = () => {
                try {
                  // 1) Strong text heuristics on body text
                  const bodyText = (document.body?.innerText || '').toLowerCase();
                  const removalPhrases = [
                    "you've been removed from this meeting",
                    'you have been removed from this meeting',
                    'removed from meeting',
                    'meeting ended',
                    'call ended'
                  ];
                  if (removalPhrases.some(p => bodyText.includes(p))) {
                    (window as any).logBot('🚨 Teams removal detected via body text');
                    return true;
                  }

                  // 2) Button heuristics
                  const buttons = Array.from(document.querySelectorAll('button')) as HTMLElement[];
                  for (const btn of buttons) {
                    const txt = (btn.textContent || btn.innerText || '').trim().toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (txt === 'rejoin' || txt === 'dismiss' || aria.includes('rejoin') || aria.includes('dismiss')) {
                      if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                        const cs = getComputedStyle(btn);
                        if (cs.display !== 'none' && cs.visibility !== 'hidden') {
                          (window as any).logBot('🚨 Teams removal detected via visible buttons (Rejoin/Dismiss)');
                          return true;
                        }
                      }
                    }
                  }

                  return false;
                } catch (error: any) {
                  (window as any).logBot(`Error checking for Teams removal: ${error.message}`);
                  return false;
                }
              };

              const checkInterval = setInterval(() => {
                // First check for removal state
                if (checkForRemoval()) {
                  (window as any).logBot("🚨 Bot has been removed from the Teams meeting. Initiating graceful leave...");
                  clearInterval(checkInterval);
                  audioService.disconnect();
                  whisperLiveService.close();
                  resolve();
                  return;
                }
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
    { 
      botConfigData: botConfig, 
      whisperUrlForBrowser: whisperLiveUrl,
      selectors: {
        participantSelectors: teamsParticipantSelectors,
        speakingClasses: teamsSpeakingClassNames,
        silenceClasses: teamsSilenceClassNames,
        containerSelectors: teamsParticipantContainerSelectors,
        nameSelectors: teamsNameSelectors,
        speakingIndicators: teamsSpeakingIndicators,
        voiceLevelSelectors: teamsVoiceLevelSelectors,
        occlusionSelectors: teamsOcclusionSelectors,
        streamTypeSelectors: teamsStreamTypeSelectors,
        audioActivitySelectors: teamsAudioActivitySelectors,
        participantIdSelectors: teamsParticipantIdSelectors,
        meetingContainerSelectors: teamsMeetingContainerSelectors
      } as any
    }
  );
  
  // Start periodic removal checking from Node.js side (does not exit the process; caller handles gracefulLeave)
  log("Starting periodic Teams removal monitoring...");
  const removalCheckInterval = setInterval(async () => {
    try {
      const isRemoved = await checkForTeamsRemoval(page);
      if (isRemoved) {
        log("🚨 Teams removal detected from Node.js side. Triggering in-page leave...");
        clearInterval(removalCheckInterval);
        try {
          await callLeaveCallback(botConfig, "removed_from_meeting");
        } catch {}
        try {
          if (typeof (page as any).performLeaveAction === 'function') {
            // no-op, performLeaveAction exists only in browser context
          }
        } catch {}
        try {
          // Attempt to click Rejoin/Dismiss to close the modal gracefully
          await page.evaluate(() => {
            const clickIfVisible = (el: HTMLElement | null) => {
              if (!el) return;
              const rect = el.getBoundingClientRect();
              const cs = getComputedStyle(el);
              if (rect.width > 0 && rect.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden') {
                el.click();
              }
            };
            const btns = Array.from(document.querySelectorAll('button')) as HTMLElement[];
            for (const b of btns) {
              const t = (b.textContent || b.innerText || '').trim().toLowerCase();
              const a = (b.getAttribute('aria-label') || '').toLowerCase();
              if (t === 'dismiss' || a.includes('dismiss')) { clickIfVisible(b); break; }
            }
          });
        } catch {}
      }
    } catch (error: any) {
      log(`Error during removal check: ${error.message}`);
    }
  }, 1500);

  // After page.evaluate finishes, cleanup services
  await whisperLiveService.cleanup();
  
  // Clear removal check interval
  clearInterval(removalCheckInterval);
};

// Prepare for recording by exposing necessary functions
const prepareForRecording = async (page: Page, botConfig: BotConfig): Promise<void> => {
  // Expose the logBot function to the browser context
  await page.exposeFunction("logBot", (msg: string) => {
    log(msg);
  });

  // Expose selectors/constants for browser context consumers
  await page.exposeFunction("getTeamsSelectors", (): { teamsPrimaryLeaveButtonSelectors: string[]; teamsSecondaryLeaveButtonSelectors: string[] } => ({
    teamsPrimaryLeaveButtonSelectors,
    teamsSecondaryLeaveButtonSelectors
  }));

  // Expose bot config for callback functions
  await page.exposeFunction("getBotConfig", (): BotConfig => botConfig);


  // Ensure leave function is available even before admission
  await page.evaluate(() => {
    if (typeof (window as any).performLeaveAction !== "function") {
      (window as any).performLeaveAction = async () => {
        try {
          // Call leave callback first to notify bot-manager
          (window as any).logBot?.("🔥 Calling leave callback before attempting to leave...");
          try {
            const botConfig = (window as any).getBotConfig?.();
            if (botConfig) {
              // We need to call the callback from Node.js context, not browser context
              // This will be handled by the Node.js side when leaveMicrosoftTeams is called
              (window as any).logBot?.("📡 Leave callback will be sent from Node.js context");
            }
          } catch (callbackError: any) {
            (window as any).logBot?.(`⚠️ Warning: Could not prepare leave callback: ${callbackError.message}`);
          }

          const sel = (window as any).getTeamsSelectors?.();
          if (sel) {
            (window as any).teamsPrimaryLeaveButtonSelectors = sel.teamsPrimaryLeaveButtonSelectors;
            (window as any).teamsSecondaryLeaveButtonSelectors = sel.teamsSecondaryLeaveButtonSelectors;
          }
          // Teams-specific leave button selectors (injected from Node via globals)
          const primaryLeaveButtonSelectors = (window as any).teamsPrimaryLeaveButtonSelectors as string[] || [];
          const secondaryLeaveButtonSelectors = (window as any).teamsSecondaryLeaveButtonSelectors as string[] || [];

          // Enhanced leave button detection with better logging
          (window as any).logBot?.("🔍 Starting Teams leave button detection...");
          
          // First, log all available buttons for debugging
          const allButtons = document.querySelectorAll('button');
          (window as any).logBot?.(`📊 Found ${allButtons.length} buttons on page`);
          
          // Log potential leave buttons for debugging
          for (const button of allButtons) {
            const ariaLabel = button.getAttribute('aria-label');
            const dataTid = button.getAttribute('data-tid');
            const id = button.getAttribute('id');
            const textContent = button.textContent?.trim();
            
            if (ariaLabel?.toLowerCase().includes('leave') || 
                ariaLabel?.toLowerCase().includes('hang') ||
                ariaLabel?.toLowerCase().includes('end') ||
                dataTid?.includes('hangup') ||
                id?.includes('hangup') ||
                textContent?.toLowerCase().includes('leave') ||
                textContent?.toLowerCase().includes('hang')) {
              (window as any).logBot?.(`🎯 Potential leave button: aria-label="${ariaLabel}", data-tid="${dataTid}", id="${id}", text="${textContent}"`);
            }
          }
          
          // Try each selector until one works
          for (const selector of primaryLeaveButtonSelectors) {
            try {
              const leaveButton = document.querySelector(selector) as HTMLElement;
              if (leaveButton) {
                (window as any).logBot?.(`🔍 Trying selector: ${selector}`);
                (window as any).logBot?.(`🔘 Button details: aria-label="${leaveButton.getAttribute('aria-label')}", data-tid="${leaveButton.getAttribute('data-tid')}", id="${leaveButton.getAttribute('id')}"`);
                
                // Try to click the button
                leaveButton.scrollIntoView({ behavior: 'smooth', block: 'center' });
                await new Promise((resolve) => setTimeout(resolve, 500));
                
                (window as any).logBot?.(`🖱️ Clicking Teams leave button...`);
                leaveButton.click();
                await new Promise((resolve) => setTimeout(resolve, 1500));
                
                // Check for confirmation dialogs
                (window as any).logBot?.(`🔍 Checking for confirmation dialogs...`);
                for (const confirmSelector of secondaryLeaveButtonSelectors) {
                  try {
                    const confirmButton = document.querySelector(confirmSelector) as HTMLElement;
                    if (confirmButton) {
                      (window as any).logBot?.(`✅ Found confirmation button: ${confirmSelector}`);
                      confirmButton.click();
                      await new Promise((resolve) => setTimeout(resolve, 1000));
                      break;
                    }
                  } catch (e) {
                    continue;
                  }
                }
                
                (window as any).logBot?.(`✅ Teams leave sequence completed successfully`);
                return true;
              }
            } catch (e: any) {
              (window as any).logBot?.(`❌ Error with selector ${selector}: ${e.message}`);
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
    // Step 1: Navigate to Teams meeting
    log(`Step 1: Navigating to Teams meeting: ${botConfig.meetingUrl}`);
    await page.goto(botConfig.meetingUrl, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(5000);
    
    // STATUS CHANGE: Bot is joining - take screenshot before JOINING callback
    await page.screenshot({ path: '/app/storage/screenshots/teams-status-joining.png', fullPage: true });
    log("📸 Screenshot taken: Bot state when JOINING callback is triggered");
    
    // --- Call joining callback to notify bot-manager that bot is joining ---
    try {
      await callJoiningCallback(botConfig);
      log("Joining callback sent successfully");
    } catch (callbackError: any) {
      log(`Warning: Failed to send joining callback: ${callbackError.message}. Continuing with join process...`);
    }

    // UI ACTION: Click "Continue on this browser" button
    log("Step 2: Looking for continue button...");
    try {
      const continueButton = page.locator(teamsContinueButtonSelectors[0]).first();
      await continueButton.waitFor({ timeout: 10000 });
      await continueButton.click();
      log("✅ Clicked continue button");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ Continue button not found, continuing...");
    }

    // UI ACTION: Click join button 
    log("Step 3: Looking for join button...");
    try {
      const joinButton = page.locator(teamsJoinButtonSelectors[0]).first();
      await joinButton.waitFor({ timeout: 10000 });
      await joinButton.click();
      log("✅ Clicked join button");
      await page.waitForTimeout(3000);
    } catch (error) {
      log("ℹ️ Join button not found, continuing...");
    }

    // UI ACTION: Try to turn off camera
    log("Step 4: Trying to turn off camera...");
    try {
      const cameraButton = page.locator(teamsCameraButtonSelectors[0]);
      await cameraButton.waitFor({ timeout: 5000 });
      await cameraButton.click();
      log("✅ Camera turned off");
    } catch (error) {
      log("ℹ️ Camera button not found or already off");
    }

    // UI ACTION: Set display name
    log("Step 5: Trying to set display name...");
    try {
      const nameInput = page.locator(teamsNameInputSelectors.join(', ')).first();
      await nameInput.waitFor({ timeout: 5000 });
      await nameInput.fill(botConfig.botName);
      log(`✅ Display name set to "${botConfig.botName}"`);
    } catch (error) {
      log("ℹ️ Display name input not found, continuing...");
    }

    // UI ACTION: Click final join button
    log("Step 6: Looking for final join button...");
    try {
      const finalJoinButton = page.locator(teamsJoinButtonSelectors.join(', ')).first();
      await finalJoinButton.waitFor({ timeout: 10000 });
      await finalJoinButton.click();
      log("✅ Clicked final join button");
      await page.waitForTimeout(5000);
    } catch (error) {
      log("ℹ️ Final join button not found");
    }

    // Check current state
    log("Step 7: Checking current state...");
    const currentUrl = page.url();
    log(`📍 Current URL: ${currentUrl}`);
    
    // Setup websocket connection and meeting admission concurrently
    log("Starting WebSocket connection while waiting for Teams meeting admission");
    try {
      // Run both processes concurrently
      const [isAdmitted] = await Promise.all([
        // Wait for admission to the Teams meeting
        waitForTeamsMeetingAdmission(page, botConfig.automaticLeave.waitingRoomTimeout, botConfig).catch((error) => {
          log("Teams meeting admission failed: " + error.message);
          return false;
        }),

        // Prepare for recording (expose functions, etc.) while waiting for admission
        prepareForRecording(page, botConfig),
      ]);
    
    if (!isAdmitted) {
      log("Bot was not admitted into the Teams meeting within the timeout period. This is a normal completion.");
      await gracefulLeaveFunction(page, 0, "admission_failed");
      return;
    }

    log("Successfully admitted to the Teams meeting, starting recording");
    
    // STATUS CHANGE: Bot is active - take screenshot before STARTUP callback
    await page.screenshot({ path: '/app/storage/screenshots/teams-status-startup.png', fullPage: true });
    log("📸 Screenshot taken: Bot state when STARTUP callback is triggered");
    
    // --- Call startup callback to notify bot-manager that bot is active ---
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
export async function leaveMicrosoftTeams(page: Page | null, botConfig?: BotConfig, reason: string = "manual_leave"): Promise<boolean> {
  log("[leaveMicrosoftTeams] Triggering leave action in browser context...");
  if (!page || page.isClosed()) {
    log("[leaveMicrosoftTeams] Page is not available or closed.");
    return false;
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
    log(`[leaveMicrosoftTeams] Browser leave action result: ${result}`);
    return result;
  } catch (error: any) {
    log(`[leaveMicrosoftTeams] Error calling performLeaveAction in browser: ${error.message}`);
    return false;
  }
}