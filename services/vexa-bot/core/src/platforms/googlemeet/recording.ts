import { Page } from "playwright";
import { log } from "../../utils";
import { BotConfig } from "../../types";
import { RecordingService } from "../../services/recording";
import { setActiveRecordingService, getSegmentPublisher } from "../../index";
import { ensureBrowserUtils } from "../../utils/injection";
import {
  googleParticipantSelectors,
  googleSpeakingClassNames,
  googleSilenceClassNames,
  googleParticipantContainerSelectors,
  googleNameSelectors,
  googleSpeakingIndicators,
  googlePeopleButtonSelectors
} from "./selectors";

// Modified to use new services - Google Meet recording functionality
export async function startGoogleRecording(page: Page, botConfig: BotConfig): Promise<void> {
  log("Starting Google Meet recording");

  // Reset segment publisher session start to align with recording start.
  // SegmentPublisher was created pre-admission; recording starts post-admission.
  // Without this reset, segment.start_time would be offset by the admission wait time.
  const publisher = getSegmentPublisher();
  if (publisher) {
    publisher.resetSessionStart();
    log(`[Recording] Session start reset to ${new Date(publisher.sessionStartMs).toISOString()}`);
  }

  const wantsAudioCapture =
    !!botConfig.recordingEnabled &&
    (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
  const sessionUid = botConfig.connectionId || `gm-${Date.now()}`;
  let recordingService: RecordingService | null = null;

  if (wantsAudioCapture) {
    recordingService = new RecordingService(botConfig.meeting_id, sessionUid);
    setActiveRecordingService(recordingService);

    await page.exposeFunction("__vexaSaveRecordingBlob", async (payload: { base64: string; mimeType?: string }) => {
      try {
        if (!recordingService) {
          log("[Google Recording] Recording service not initialized; dropping blob.");
          return false;
        }

        const mimeType = (payload?.mimeType || "").toLowerCase();
        let format = "webm";
        if (mimeType.includes("wav")) format = "wav";
        else if (mimeType.includes("ogg")) format = "ogg";
        else if (mimeType.includes("mp4") || mimeType.includes("m4a")) format = "m4a";

        const blobBuffer = Buffer.from(payload.base64 || "", "base64");
        if (!blobBuffer.length) {
          log("[Google Recording] Received empty audio blob.");
          return false;
        }

        await recordingService.writeBlob(blobBuffer, format);
        log(`[Google Recording] Saved browser audio blob (${blobBuffer.length} bytes, ${format}).`);
        return true;
      } catch (error: any) {
        log(`[Google Recording] Failed to persist browser blob: ${error?.message || String(error)}`);
        return false;
      }
    });

    // Pack B (issue #218): incremental chunk upload — each MediaRecorder
    // chunk uploads immediately, so ungraceful exits leave already-uploaded
    // chunks durable in MinIO. `is_final=false` on intermediate chunks;
    // shutdown-path flush sends the last chunk with `is_final=true`.
    await page.exposeFunction(
      "__vexaSaveRecordingChunk",
      async (payload: { base64: string; chunkSeq: number; isFinal: boolean; mimeType?: string }) => {
        try {
          if (!recordingService) {
            log("[Google Recording] Recording service not initialized; dropping chunk.");
            return false;
          }
          if (!botConfig.recordingUploadUrl || !botConfig.token) {
            // No upload URL configured — fall back to the buffered path.
            return false;
          }
          const mimeType = (payload?.mimeType || "").toLowerCase();
          let format = "webm";
          if (mimeType.includes("wav")) format = "wav";
          else if (mimeType.includes("ogg")) format = "ogg";
          else if (mimeType.includes("mp4") || mimeType.includes("m4a")) format = "m4a";

          const buf = Buffer.from(payload.base64 || "", "base64");
          if (!buf.length) return false;

          await recordingService.uploadChunk(
            botConfig.recordingUploadUrl,
            botConfig.token,
            buf,
            payload.chunkSeq,
            !!payload.isFinal,
            format,
          );
          return true;
        } catch (error: any) {
          log(`[Google Recording] uploadChunk failed (seq=${payload?.chunkSeq}): ${error?.message || String(error)}`);
          return false;
        }
      },
    );
  } else {
    log("[Google Recording] Audio capture disabled by config.");
  }

  // Expose callback so the browser can signal when MediaRecorder actually starts.
  // This re-aligns sessionStartMs with the recording, fixing click-to-seek offset.
  await page.exposeFunction("__vexaRecordingStarted", () => {
    if (publisher) {
      publisher.resetSessionStart();
      log(`[Recording] Session start re-aligned to MediaRecorder start: ${new Date(publisher.sessionStartMs).toISOString()}`);
    }
  });

  await ensureBrowserUtils(page, require('path').join(__dirname, '../../browser-utils.global.js'));

  // Pass the necessary config fields and the resolved URL into the page context
  await page.evaluate(
    async (pageArgs: {
      botConfigData: BotConfig;
      selectors: {
        participantSelectors: string[];
        speakingClasses: string[];
        silenceClasses: string[];
        containerSelectors: string[];
        nameSelectors: string[];
        speakingIndicators: string[];
        peopleButtonSelectors: string[];
      };
    }) => {
      const { botConfigData, selectors } = pageArgs;

      // Use browser utility classes from the global bundle
      const browserUtils = (window as any).VexaBrowserUtils;
      (window as any).logBot(`Browser utils available: ${Object.keys(browserUtils || {}).join(', ')}`);

      const audioService = new browserUtils.BrowserAudioService({
        targetSampleRate: 16000,
        bufferSize: 4096,
        inputChannels: 1,
        outputChannels: 1
      });

      (window as any).__vexaAudioService = audioService;
      (window as any).__vexaBotConfig = botConfigData;
      (window as any).__vexaMediaRecorder = null;
      (window as any).__vexaRecordedChunks = [];
      (window as any).__vexaRecordingFlushed = false;

      const isAudioRecordingEnabled =
        !!(botConfigData as any)?.recordingEnabled &&
        (!Array.isArray((botConfigData as any)?.captureModes) ||
          (botConfigData as any)?.captureModes.includes("audio"));

      const getSupportedMediaRecorderMimeType = (): string => {
        const candidates = [
          "audio/webm;codecs=opus",
          "audio/webm",
          "audio/ogg;codecs=opus",
          "audio/ogg",
        ];
        for (const mime of candidates) {
          try {
            if ((window as any).MediaRecorder?.isTypeSupported?.(mime)) {
              return mime;
            }
          } catch {}
        }
        return "";
      };

      const flushBrowserRecordingBlob = async (reason: string): Promise<void> => {
        if (!isAudioRecordingEnabled) return;
        if ((window as any).__vexaRecordingFlushed) return;

        try {
          const recorder: MediaRecorder | null = (window as any).__vexaMediaRecorder;
          const chunks: Blob[] = (window as any).__vexaRecordedChunks || [];

          const finalizeAndSend = async () => {
            if ((window as any).__vexaRecordingFlushed) return;
            (window as any).__vexaRecordingFlushed = true;

            try {
              // Pack B (issue #218): with incremental chunk upload in the
              // ondataavailable handler, the preferred shutdown path is a
              // single final-chunk POST with is_final=true. The `recorder.stop()`
              // call just before this flush triggers one last dataavailable
              // event that already uploaded as is_final=false; now we send
              // an empty-body "finalizer" with the next chunk_seq so
              // meeting-api flips the Recording to COMPLETED and fires the
              // webhook. If that fails (or chunk sink absent), fall back to
              // the legacy full-blob save for durable local copy.
              const mimeType =
                (window as any).__vexaMediaRecorder?.mimeType || "audio/webm";
              const chunkSeq = ((window as any).__vexaChunkSeq as number) ?? 0;

              let chunkFinalized = false;
              if (typeof (window as any).__vexaSaveRecordingChunk === "function") {
                try {
                  await (window as any).__vexaSaveRecordingChunk({
                    base64: "",
                    chunkSeq,
                    isFinal: true,
                    mimeType,
                  });
                  (window as any).logBot?.(
                    `[Google Recording] Finalized recording (chunk_seq=${chunkSeq}, ${reason}).`
                  );
                  chunkFinalized = true;
                } catch (err: any) {
                  (window as any).logBot?.(
                    `[Google Recording] Chunk finalizer failed: ${err?.message || err}`
                  );
                }
              }

              // Legacy full-blob path (kept as defense-in-depth for local
              // disk copy + for cases where chunk upload is unreachable).
              const recorded = (window as any).__vexaRecordedChunks || [];
              if (recorded.length) {
                const blob = new Blob(recorded, { type: mimeType });
                const buffer = await blob.arrayBuffer();
                const bytes = new Uint8Array(buffer);
                let binary = "";
                const chunkSize = 0x8000;
                for (let i = 0; i < bytes.length; i += chunkSize) {
                  binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
                }
                const base64 = btoa(binary);

                if (typeof (window as any).__vexaSaveRecordingBlob === "function") {
                  await (window as any).__vexaSaveRecordingBlob({
                    base64,
                    mimeType: blob.type || mimeType,
                  });
                  (window as any).logBot?.(
                    `[Google Recording] Flushed ${bytes.length} bytes (${blob.type || mimeType}) on ${reason}${chunkFinalized ? " (chunk upload already final)" : ""}.`
                  );
                } else if (!chunkFinalized) {
                  (window as any).logBot?.("[Google Recording] Node blob sink is not available.");
                }
              } else if (!chunkFinalized) {
                (window as any).logBot?.(`[Google Recording] No media chunks to flush (${reason}).`);
              }
            } catch (err: any) {
              (window as any).logBot?.(
                `[Google Recording] Failed to flush blob: ${err?.message || err}`
              );
            } finally {
              (window as any).__vexaRecordedChunks = [];
            }
          };

          if (recorder && recorder.state !== "inactive") {
            await new Promise<void>((resolveStop) => {
              const onStop = async () => {
                recorder.removeEventListener("stop", onStop as any);
                await finalizeAndSend();
                resolveStop();
              };
              recorder.addEventListener("stop", onStop as any, { once: true });
              try {
                recorder.stop();
              } catch {
                // Recorder may already be stopping; resolve after a short delay.
                setTimeout(async () => {
                  await finalizeAndSend();
                  resolveStop();
                }, 200);
              }
            });
          } else if (chunks.length > 0) {
            await finalizeAndSend();
          }
        } catch (err: any) {
          (window as any).logBot?.(
            `[Google Recording] Unexpected flush error: ${err?.message || err}`
          );
        }
      };

      (window as any).__vexaFlushRecordingBlob = flushBrowserRecordingBlob;

      await new Promise<void>((resolve, reject) => {
        try {
          (window as any).logBot("Starting Google Meet recording process with new services.");
          
          // Wait a bit for media elements to initialize after admission, then start the chain
          (async () => {
            let degradedNoMedia = false;
            // Wait 2 seconds for media elements to initialize after admission
            (window as any).logBot("Waiting 2 seconds for media elements to initialize after admission...");
            await new Promise(resolve => setTimeout(resolve, 2000));
            
            // Find and create combined audio stream with enhanced retry logic
            // Use 10 retries with 3s delay = 30s total wait time
            audioService.findMediaElements(10, 3000).then(async (mediaElements: HTMLMediaElement[]) => {
            if (mediaElements.length === 0) {
              degradedNoMedia = true;
              (window as any).logBot(
                "[Google Meet BOT Warning] No active media elements found after retries; " +
                "continuing in degraded monitoring mode (session remains active)."
              );
              return undefined;
            }

            // Create combined audio stream
            return await audioService.createCombinedAudioStream(mediaElements);
          }).then(async (combinedStream: MediaStream | undefined) => {
            if (!combinedStream) {
              if (!degradedNoMedia) {
                reject(new Error("[Google Meet BOT Error] Failed to create combined audio stream"));
                return;
              }
              return null;
            }

            if (isAudioRecordingEnabled) {
              try {
                const mimeType = getSupportedMediaRecorderMimeType();
                const recorderOptions = mimeType ? ({ mimeType } as MediaRecorderOptions) : undefined;
                const recorder = recorderOptions
                  ? new MediaRecorder(combinedStream, recorderOptions)
                  : new MediaRecorder(combinedStream);

                (window as any).__vexaMediaRecorder = recorder;
                (window as any).__vexaRecordedChunks = [];
                (window as any).__vexaRecordingFlushed = false;
                (window as any).__vexaChunkSeq = 0;

                // v0.10.5.3 Pack M (issue #294 follow-up): upload each chunk
                // immediately to meeting-api. Keep the buffer ONLY for chunks
                // that are still pending upload — splice on success, cap at
                // VEXA_RECORDED_CHUNKS_CAP. Pre-v0.10.5.3 this buffer accumulated
                // every chunk for the entire meeting lifetime as a "fallback for
                // shutdown-flush", growing to 10+ MB on real-conversation
                // 24-min meetings and contributing to the tab-process pressure
                // that triggered page-navigation crashes.
                //
                // No fallback path here per the no-fallbacks rule (Pack P).
                // The fallback case (chunk in buffer that never uploaded) is
                // handled by post_meeting_reconciler running server-side, which
                // re-fetches missing chunks from S3 — the bot doesn't need to
                // hold them. If __vexaSaveRecordingChunk returns false (sink
                // rejected), we surface the error and DROP the chunk from the
                // browser-side buffer regardless; meeting-api's chunk_seq
                // contract handles resync via the reconciler.
                const VEXA_RECORDED_CHUNKS_CAP = 10;
                recorder.ondataavailable = async (event: BlobEvent) => {
                  if (!(event.data && event.data.size > 0)) {
                    (window as any).logBot?.("[Google Recording] dataavailable fired with empty data (skipping)");
                    return;
                  }
                  const buffer = (window as any).__vexaRecordedChunks as Blob[];
                  buffer.push(event.data);
                  // Defensive cap — should never trip in normal operation
                  // because successful uploads splice. If it trips, log and
                  // drop the oldest, surface as a metric for Pack T to
                  // capture.
                  if (buffer.length > VEXA_RECORDED_CHUNKS_CAP) {
                    const dropped = buffer.shift();
                    (window as any).logBot?.(
                      `[Google Recording] WARN __vexaRecordedChunks exceeded cap ${VEXA_RECORDED_CHUNKS_CAP}, dropped oldest (${dropped?.size ?? 0} bytes); reconciler will re-fetch from S3`
                    );
                  }

                  // Best-effort immediate upload; do NOT block the recorder.
                  const chunkSeq = (window as any).__vexaChunkSeq as number;
                  (window as any).__vexaChunkSeq = chunkSeq + 1;
                  try {
                    const mimeType = recorder.mimeType || "audio/webm";
                    const arrBuffer = await event.data.arrayBuffer();
                    const bytes = new Uint8Array(arrBuffer);
                    let binary = "";
                    const encodeChunkSize = 0x8000;
                    for (let i = 0; i < bytes.length; i += encodeChunkSize) {
                      binary += String.fromCharCode(...bytes.subarray(i, i + encodeChunkSize));
                    }
                    const base64 = btoa(binary);
                    if (typeof (window as any).__vexaSaveRecordingChunk === "function") {
                      (window as any).logBot?.(
                        `[Google Recording] Uploading chunk ${chunkSeq} (${bytes.length} bytes)`
                      );
                      // Await so a thrown error surfaces. Failures here
                      // previously went silent (fire-and-forget), which
                      // masked compose recording regression in Bug A 2026-04-21.
                      const ok = await (window as any).__vexaSaveRecordingChunk({
                        base64,
                        chunkSeq,
                        isFinal: false,
                        mimeType,
                      });
                      // Pack M (#294-followup): splice the chunk out of the
                      // buffer regardless of upload result. If ok=true the
                      // chunk is durable in S3. If ok=false (sink rejected),
                      // post_meeting_reconciler re-fetches from S3 server-side.
                      // Either way, the browser-side buffer doesn't need to
                      // keep it.
                      const idx = buffer.indexOf(event.data);
                      if (idx >= 0) buffer.splice(idx, 1);
                      if (!ok) {
                        (window as any).logBot?.(
                          `[Google Recording] Chunk ${chunkSeq} upload returned false — bot-side sink rejected (see bot-container logs); reconciler will re-fetch`
                        );
                      }
                    } else {
                      // No upload function exposed — leave chunk in buffer for
                      // a single 30s window then drop. This is a misconfig path
                      // (page.exposeBinding never ran), not a runtime fallback.
                      (window as any).logBot?.(
                        `[Google Recording] WARN __vexaSaveRecordingChunk not exposed — chunk ${chunkSeq} cannot be uploaded; buffer cap will drop on next chunk`
                      );
                    }
                  } catch (err: any) {
                    // On exception during upload prep/send, splice the chunk
                    // anyway — the reconciler covers re-fetch.
                    const idx = buffer.indexOf(event.data);
                    if (idx >= 0) buffer.splice(idx, 1);
                    (window as any).logBot?.(
                      `[Google Recording] Chunk ${chunkSeq} upload prep/send FAILED: ${err?.message || err}; spliced from buffer; reconciler will re-fetch`
                    );
                  }
                };

                // 30-second chunks: balances upload overhead with data-loss
                // granularity on SIGKILL. Rarely shorter chunks are also fine
                // — the server-side endpoint appends by chunk_seq.
                recorder.start(30000);
                // Signal Node.js that recording started — re-aligns segment timestamps
                (window as any).__vexaRecordingStarted?.();
                (window as any).logBot?.(
                  `[Google Recording] MediaRecorder started (${recorder.mimeType || mimeType || "default"}).`
                );
              } catch (err: any) {
                (window as any).logBot?.(
                  `[Google Recording] Failed to start MediaRecorder: ${err?.message || err}`
                );
              }
            }

            // Initialize audio processor
            return await audioService.initializeAudioProcessor(combinedStream);
          }).then(async (processor: any) => {
            if (!processor) {
              return null;
            }
            // Setup audio data processing
            // v0.10.5 #285 Layer 2 — track audio energy for cross-validation against
            // the DOM participant counter. If audio is flowing in the last 120s, the
            // meeting has speakers regardless of what the DOM count says — so we
            // refuse to fire LEFT_ALONE_TIMEOUT under those conditions. Closes the
            // failure mode where DOM-only heuristic returns 0 while transcription
            // pipeline is actively producing segments.
            (window as any).__vexaLastAudioActivityTs = 0;
            const AUDIO_ACTIVITY_THRESHOLD = 0.005; // RMS above silence baseline
            audioService.setupAudioDataProcessor(async (audioData: Float32Array, _sessionStartTime: number | null) => {
              // Per-speaker pipeline (speaker-streams.ts) handles transcription.
              // This processor checks audio energy for the alone-cross-validation hook.
              if (!audioData || audioData.length === 0) return;
              try {
                let maxAbs = 0;
                // Cheap scan: 1-of-32 sample stride is plenty to detect non-silence
                for (let i = 0; i < audioData.length; i += 32) {
                  const v = Math.abs(audioData[i]);
                  if (v > maxAbs) maxAbs = v;
                  if (maxAbs > AUDIO_ACTIVITY_THRESHOLD) break;
                }
                if (maxAbs > AUDIO_ACTIVITY_THRESHOLD) {
                  (window as any).__vexaLastAudioActivityTs = Date.now();
                }
              } catch {}
            });

            return null;
          }).then(() => {
            // Initialize Google-specific speaker detection (Teams-style with Google selectors)
            if (!degradedNoMedia) {
              (window as any).logBot("Initializing Google Meet speaker detection...");
            }

            const initializeGoogleSpeakerDetection = (audioService: any, botConfigData: any) => {
              const selectorsTyped = selectors as any;

              const speakingStates = new Map<string, string>();
              function hashStr(s: string): string {
                // small non-crypto hash to avoid logging PII
                let h = 5381;
                for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i);
                return (h >>> 0).toString(16).slice(0, 8);
              }

              function getGoogleParticipantId(element: HTMLElement) {
                let id = element.getAttribute('data-participant-id');
                if (!id) {
                  const stableChild = element.querySelector('[jsinstance]') as HTMLElement | null;
                  if (stableChild) {
                    id = stableChild.getAttribute('jsinstance') || undefined as any;
                  }
                }
                if (!id) {
                  if (!(element as any).dataset.vexaGeneratedId) {
                    (element as any).dataset.vexaGeneratedId = 'gm-id-' + Math.random().toString(36).substr(2, 9);
                  }
                  id = (element as any).dataset.vexaGeneratedId;
                }
                return id as string;
              }

              function getGoogleParticipantName(participantElement: HTMLElement) {
                // Prefer explicit Meet name spans
                const notranslate = participantElement.querySelector('span.notranslate') as HTMLElement | null;
                if (notranslate && notranslate.textContent && notranslate.textContent.trim()) {
                  const t = notranslate.textContent.trim();
                  if (t.length > 1 && t.length < 50) return t;
                }

                // Try configured name selectors
                const nameSelectors: string[] = selectorsTyped.nameSelectors || [];
                for (const sel of nameSelectors) {
                  const el = participantElement.querySelector(sel) as HTMLElement | null;
                  if (el) {
                    let nameText = el.textContent || el.innerText || el.getAttribute('data-self-name') || el.getAttribute('aria-label') || '';
                    if (nameText) {
                      nameText = nameText.trim();
                      if (nameText && nameText.length > 1 && nameText.length < 50) return nameText;
                    }
                  }
                }

                // Helper: reject junk names (fallback-generated IDs, not real names)
                const isJunkName = (name: string): boolean => {
                  return /^Google Participant \(/.test(name) ||
                         /spaces\//.test(name) ||
                         /devices\//.test(name);
                };

                // Fallbacks
                const selfName = participantElement.getAttribute('data-self-name');
                if (selfName && selfName.trim() && !isJunkName(selfName.trim())) return selfName.trim();

                // aria-label on the container or any descendant (catches Spaces/Chat device participants)
                const ariaLabel = participantElement.getAttribute('aria-label');
                if (ariaLabel && ariaLabel.trim().length > 1 && ariaLabel.trim().length < 50 && !isJunkName(ariaLabel.trim())) return ariaLabel.trim();
                const ariaChild = participantElement.querySelector('[aria-label]') as HTMLElement | null;
                if (ariaChild) {
                  const childLabel = ariaChild.getAttribute('aria-label')?.trim();
                  if (childLabel && childLabel.length > 1 && childLabel.length < 50 && !isJunkName(childLabel)) return childLabel;
                }

                // data-tooltip on any descendant
                const tooltipEl = participantElement.querySelector('[data-tooltip]') as HTMLElement | null;
                if (tooltipEl) {
                  const tooltip = tooltipEl.getAttribute('data-tooltip')?.trim();
                  if (tooltip && tooltip.length > 1 && tooltip.length < 50 && !isJunkName(tooltip)) return tooltip;
                }

                const idToDisplay = getGoogleParticipantId(participantElement);
                return `Google Participant (${idToDisplay})`;
              }

              function isVisible(el: HTMLElement): boolean {
                const cs = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                return (
                  rect.width > 0 &&
                  rect.height > 0 &&
                  cs.display !== 'none' &&
                  cs.visibility !== 'hidden' &&
                  cs.opacity !== '0' &&
                  !ariaHidden
                );
              }

              function hasSpeakingIndicator(container: HTMLElement): boolean {
                const indicators: string[] = selectorsTyped.speakingIndicators || [];
                for (const sel of indicators) {
                  const ind = container.querySelector(sel) as HTMLElement | null;
                  if (ind && isVisible(ind)) return true;
                }
                return false;
              }

              function inferSpeakingFromClasses(container: HTMLElement, mutatedClassList?: DOMTokenList): { speaking: boolean } {
                const speakingClasses: string[] = selectorsTyped.speakingClasses || [];
                const silenceClasses: string[] = selectorsTyped.silenceClasses || [];

                const classList = mutatedClassList || container.classList;
                const descendantSpeaking = speakingClasses.some(cls => container.querySelector('.' + cls));
                const hasSpeaking = speakingClasses.some(cls => classList.contains(cls)) || descendantSpeaking;
                const hasSilent = silenceClasses.some(cls => classList.contains(cls));
                if (hasSpeaking) return { speaking: true };
                if (hasSilent) return { speaking: false };
                return { speaking: false };
              }

              function sendGoogleSpeakerEvent(eventType: string, participantElement: HTMLElement) {
                const sessionStartTime = audioService.getSessionAudioStartTime();
                if (sessionStartTime === null) {
                  return;
                }
                const relativeTimestampMs = Date.now() - sessionStartTime;
                const participantId = getGoogleParticipantId(participantElement);
                const participantName = getGoogleParticipantName(participantElement);
                // Accumulate for persistence (direct bot accumulation)
                (window as any).__vexaSpeakerEvents = (window as any).__vexaSpeakerEvents || [];
                (window as any).__vexaSpeakerEvents.push({
                  event_type: eventType,
                  participant_name: participantName,
                  participant_id: participantId,
                  relative_timestamp_ms: relativeTimestampMs,
                });
              }

              // Debug: log all class mutations to discover current Google Meet speaking classes
              let classMutationCount = 0;
              function debugClassMutation(participantElement: HTMLElement, mutatedClassList?: DOMTokenList) {
                classMutationCount++;
                // Log first 20 mutations and then every 50th to avoid spam
                if (classMutationCount <= 20 || classMutationCount % 50 === 0) {
                  const id = getGoogleParticipantId(participantElement);
                  const name = getGoogleParticipantName(participantElement);
                  const classes = mutatedClassList ? Array.from(mutatedClassList).join(' ') : '(no classList)';
                  (window as any).logBot(`[SpeakerDebug] #${classMutationCount} ${name} (${id}): classes=[${classes}]`);
                }
              }

              function logGoogleSpeakerEvent(participantElement: HTMLElement, mutatedClassList?: DOMTokenList) {
                const participantId = getGoogleParticipantId(participantElement);
                const participantName = getGoogleParticipantName(participantElement);
                const previousLogicalState = speakingStates.get(participantId) || 'silent';

                // Debug: log class mutations
                debugClassMutation(participantElement, mutatedClassList);

                // Primary: indicators; Fallback: classes
                const indicatorSpeaking = hasSpeakingIndicator(participantElement);
                const classInference = inferSpeakingFromClasses(participantElement, mutatedClassList);
                const isCurrentlySpeaking = indicatorSpeaking || classInference.speaking;

                if (isCurrentlySpeaking) {
                  if (previousLogicalState !== 'speaking') {
                    (window as any).logBot(`[SpeakerDebug] SPEAKING START: ${participantName} (indicator=${indicatorSpeaking}, classInference=${classInference.speaking})`);
                    sendGoogleSpeakerEvent('SPEAKER_START', participantElement);
                  }
                  speakingStates.set(participantId, 'speaking');
                } else {
                  if (previousLogicalState === 'speaking') {
                    (window as any).logBot(`[SpeakerDebug] SPEAKING END: ${participantName}`);
                    sendGoogleSpeakerEvent('SPEAKER_END', participantElement);
                  }
                  speakingStates.set(participantId, 'silent');
                }
              }

              function observeGoogleParticipant(participantElement: HTMLElement) {
                const participantId = getGoogleParticipantId(participantElement);
                speakingStates.set(participantId, 'silent');

                // Initial scan
                logGoogleSpeakerEvent(participantElement);

                const callback = function(mutationsList: MutationRecord[]) {
                  for (const mutation of mutationsList) {
                    if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                      const targetElement = mutation.target as HTMLElement;
                      if (participantElement.contains(targetElement) || participantElement === targetElement) {
                        logGoogleSpeakerEvent(participantElement, targetElement.classList);
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

              function scanForAllGoogleParticipants() {
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                // Debug: dump participant tile structure on first scan
                (window as any).logBot(`[SpeakerDebug] Scanning for participants with selectors: ${participantSelectors.join(', ')}`);
                let foundCount = 0;
                for (const sel of participantSelectors) {
                  document.querySelectorAll(sel).forEach((el) => {
                    foundCount++;
                    const elh = el as HTMLElement;
                    const outerClasses = elh.className;
                    const childClasses = Array.from(elh.querySelectorAll('*')).slice(0, 5).map(c => (c as HTMLElement).className).filter(Boolean);
                    (window as any).logBot(`[SpeakerDebug] Participant tile (${sel}): classes=[${outerClasses}], children=[${childClasses.join(' | ')}], innerHTML=${elh.innerHTML.substring(0, 200)}`);
                  });
                }
                (window as any).logBot(`[SpeakerDebug] Found ${foundCount} participant tiles total`);
                for (const sel of participantSelectors) {
                  document.querySelectorAll(sel).forEach((el) => {
                    const elh = el as HTMLElement;
                    if (!(elh as any).dataset.vexaObserverAttached) {
                      observeGoogleParticipant(elh);
                    }
                  });
                }
              }

              // Attempt to click People button to stabilize DOM if available
              try {
                const peopleSelectors: string[] = selectorsTyped.peopleButtonSelectors || [];
                for (const sel of peopleSelectors) {
                  const btn = document.querySelector(sel) as HTMLElement | null;
                  if (btn && isVisible(btn)) { btn.click(); break; }
                }
              } catch {}

              // Initialize
              scanForAllGoogleParticipants();

              // Expose participant name lookup to Node (used by speaker-identity.ts)
              // Returns a map of all known participant names from DOM tiles,
              // keyed by participant-id, plus a list of currently-speaking names.
              (window as any).__vexaGetAllParticipantNames = (): { names: Record<string, string>; speaking: string[] } => {
                const names: Record<string, string> = {};
                const speaking: string[] = [];
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                const seen = new Set<string>();
                participantSelectors.forEach(sel => {
                  document.querySelectorAll(sel).forEach(el => {
                    const elh = el as HTMLElement;
                    const id = getGoogleParticipantId(elh);
                    if (seen.has(id)) return;
                    seen.add(id);
                    const name = getGoogleParticipantName(elh);
                    names[id] = name;
                    if (speakingStates.get(id) === 'speaking') {
                      speaking.push(name);
                    }
                  });
                });
                return { names, speaking };
              };

              // Polling fallback to catch speaking indicators not driven by class mutations
              const lastSpeakingById = new Map<string, boolean>();
              setInterval(() => {
                const participantSelectors: string[] = selectorsTyped.participantSelectors || [];
                const elements: HTMLElement[] = [];
                participantSelectors.forEach(sel => {
                  document.querySelectorAll(sel).forEach(el => elements.push(el as HTMLElement));
                });
                elements.forEach((container) => {
                  const id = getGoogleParticipantId(container);
                  const indicatorSpeaking = hasSpeakingIndicator(container) || inferSpeakingFromClasses(container).speaking;
                  const prev = lastSpeakingById.get(id) || false;
                  if (indicatorSpeaking && !prev) {
                    // Poll speaker start — debug level
                    sendGoogleSpeakerEvent('SPEAKER_START', container);
                    lastSpeakingById.set(id, true);
                    speakingStates.set(id, 'speaking');
                  } else if (!indicatorSpeaking && prev) {
                    // Poll speaker end — debug level
                    sendGoogleSpeakerEvent('SPEAKER_END', container);
                    lastSpeakingById.set(id, false);
                    speakingStates.set(id, 'silent');
                  } else if (!lastSpeakingById.has(id)) {
                    lastSpeakingById.set(id, indicatorSpeaking);
                  }
                });
              }, 500);
            };

            if (!degradedNoMedia) {
              initializeGoogleSpeakerDetection(audioService, botConfigData);
            }

            // Participant counting: uses data-participant-id tiles, but falls back to
            // "Leave call" button visibility to avoid false-positive "alone" during screen share.
            // Google Meet removes participant tiles from the DOM during presentation mode,
            // but the "Leave call" button remains visible as long as the bot is in the meeting.
            (window as any).logBot("Initializing participant counting (data-participant-id + leave-button fallback)...");

            let lastKnownParticipantCount = 0;

            // v0.10.5 #285 Layer 1 — multi-selector fallback for participant counting.
            // Single-selector `[data-participant-id]` is fragile to GMeet UI changes;
            // any rename/relocation of that attribute takes the count to 0 globally
            // and triggers false LEFT_ALONE_TIMEOUT. Add parallel selectors so a single
            // DOM-shape change doesn't take the count to 0:
            //   - `[role="region"][aria-label*="articipant"]` — tile region heuristic
            //     (matches "participant" / "Participant" / "participants")
            //   - `[data-self-name]` — bot's own self-tile (always present once admitted)
            const countParticipantTiles = (): number => {
              const ids = new Set<string>();
              // Primary: data-participant-id tiles
              document.querySelectorAll('[data-participant-id]').forEach((el: Element) => {
                const id = el.getAttribute('data-participant-id');
                if (id) ids.add(id);
              });
              // Fallback 1: aria-labelled participant regions
              try {
                document.querySelectorAll('[role="region"][aria-label*="articipant"]').forEach((el: Element, idx: number) => {
                  const label = el.getAttribute('aria-label') || `region-${idx}`;
                  ids.add(`region-fallback:${label}`);
                });
              } catch {}
              // Fallback 2: self-name marker — always present after admission
              try {
                document.querySelectorAll('[data-self-name]').forEach((el: Element) => {
                  const name = el.getAttribute('data-self-name') || 'self';
                  ids.add(`self-fallback:${name}`);
                });
              } catch {}
              return ids.size;
            };

            const isBotStillInMeeting = (): boolean => {
              // "Leave call" button is the most reliable signal — it's always visible while in a meeting
              const leaveBtn = document.querySelector('button[aria-label*="Leave call"]');
              return leaveBtn !== null;
            };

            (window as any).getGoogleMeetActiveParticipants = () => {
              const tileCount = countParticipantTiles();
              const inMeeting = isBotStillInMeeting();
              // If tiles show 0 but we're still in the meeting (e.g. screen share mode),
              // keep the last known count (minimum 2) to avoid false "alone" triggers
              if (tileCount === 0 && inMeeting && lastKnownParticipantCount > 1) {
                (window as any).logBot(`🔍 [Google Meet Participants] 0 tiles but Leave button present — keeping last count ${lastKnownParticipantCount} (screen share mode)`);
                return new Array(lastKnownParticipantCount).fill('placeholder');
              }
              if (tileCount > 0) {
                lastKnownParticipantCount = tileCount;
              }
              // Only log participant count changes, not every poll
              if (tileCount !== lastKnownParticipantCount) {
                (window as any).logBot(`🔍 [Google Meet Participants] ${tileCount} tiles, inMeeting=${inMeeting}`);
              }
              return new Array(tileCount).fill('placeholder');
            };
            (window as any).getGoogleMeetActiveParticipantsCount = () => {
              return (window as any).getGoogleMeetActiveParticipants().length;
            };
            
            // Setup Google Meet meeting monitoring (browser context)
            const setupGoogleMeetingMonitoring = (botConfigData: any, audioService: any, resolve: any) => {
              (window as any).logBot("Setting up Google Meet meeting monitoring...");
              
              const leaveCfg = (botConfigData && (botConfigData as any).automaticLeave) || {};
              // Config values are in milliseconds, convert to seconds
              const startupAloneTimeoutSeconds = leaveCfg.noOneJoinedTimeout
                ? Math.floor(Number(leaveCfg.noOneJoinedTimeout) / 1000)
                : Number(leaveCfg.startupAloneTimeoutSeconds ?? (20 * 60));
              const everyoneLeftTimeoutSeconds = leaveCfg.everyoneLeftTimeout
                ? Math.floor(Number(leaveCfg.everyoneLeftTimeout) / 1000)
                : Number(leaveCfg.everyoneLeftTimeoutSeconds ?? 60);
              
              let aloneTime = 0;
              let lastParticipantCount = 0;
              let speakersIdentified = false;
              let hasEverHadMultipleParticipants = false;
              let monitoringStopped = false;

              const stopWithFlush = async (
                reason: string,
                finish: () => void
              ) => {
                if (monitoringStopped) return;
                monitoringStopped = true;
                clearInterval(checkInterval);
                try {
                  if (typeof (window as any).__vexaFlushRecordingBlob === "function") {
                    await (window as any).__vexaFlushRecordingBlob(reason);
                  }
                } catch (flushErr: any) {
                  (window as any).logBot?.(
                    `[Google Recording] Flush error during shutdown (${reason}): ${flushErr?.message || flushErr}`
                  );
                }
                audioService.disconnect();
                finish();
              };

              const checkInterval = setInterval(() => {
                // Check participant count using the comprehensive helper
                const currentParticipantCount = (window as any).getGoogleMeetActiveParticipantsCount ? (window as any).getGoogleMeetActiveParticipantsCount() : 0;
                
                if (currentParticipantCount !== lastParticipantCount) {
                  (window as any).logBot(`Participant check: Found ${currentParticipantCount} unique participants from central list.`);
                  lastParticipantCount = currentParticipantCount;
                  
                  // Track if we've ever had multiple participants
                  if (currentParticipantCount > 1) {
                    hasEverHadMultipleParticipants = true;
                    speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                    (window as any).logBot("Speakers identified - switching to post-speaker monitoring mode");
                  }
                }

                if (currentParticipantCount <= 1) {
                  // v0.10.5 #285 Layer 2 — audio cross-validate before incrementing
                  // aloneTime. If audio activity within last 120s, the meeting has
                  // speakers regardless of what the DOM count says (DOM heuristic
                  // is single-selector fragile; audio is independent ground truth).
                  // Two independent signals can't both fail in the same way without
                  // something architecturally wrong.
                  const lastAudioMs = (window as any).__vexaLastAudioActivityTs || 0;
                  const audioRecentlyActive = lastAudioMs > 0 && (Date.now() - lastAudioMs) < 120000;
                  if (audioRecentlyActive) {
                    if (aloneTime > 0) {
                      (window as any).logBot(
                        `🎤 [Google Meet Cross-Validate] DOM count=${currentParticipantCount} but audio activity ${Math.round((Date.now() - lastAudioMs)/1000)}s ago — refusing to count alone-time (false-LEFT_ALONE guard)`
                      );
                    }
                    aloneTime = 0;
                    return; // skip alone-time block entirely this tick
                  }
                  aloneTime++;

                  // Determine timeout based on whether speakers have been identified
                  const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                  const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";

                  if (aloneTime >= currentTimeout) {
                    if (speakersIdentified) {
                      (window as any).logBot(`Google Meet meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                      void stopWithFlush("left_alone_timeout", () =>
                        reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT"))
                      );
                    } else {
                      (window as any).logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds/60} minutes during startup with no other participants. Stopping recorder...`);
                      void stopWithFlush("startup_alone_timeout", () =>
                        reject(new Error("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT"))
                      );
                    }
                  } else if (aloneTime > 0 && aloneTime % 10 === 0) { // Log every 10 seconds to avoid spam
                    if (speakersIdentified) {
                      (window as any).logBot(`Bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                    } else {
                      const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                      const remainingSeconds = (currentTimeout - aloneTime) % 60;
                      (window as any).logBot(`Bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                    }
                  }
                } else {
                  aloneTime = 0; // Reset if others are present
                  if (hasEverHadMultipleParticipants && !speakersIdentified) {
                    speakersIdentified = true;
                    (window as any).logBot("Speakers identified - switching to post-speaker monitoring mode");
                  }
                }
              }, 1000);

              // Listen for page unload
              window.addEventListener("beforeunload", () => {
                (window as any).logBot("Page is unloading. Stopping recorder...");
                void stopWithFlush("beforeunload", () => resolve());
              });

              document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "hidden") {
                  (window as any).logBot("Document is hidden. Stopping recorder...");
                  void stopWithFlush("visibility_hidden", () => resolve());
                }
              });
            };

            setupGoogleMeetingMonitoring(botConfigData, audioService, resolve);
          }).catch((err: any) => {
            reject(err);
          });
          })(); // Close async IIFE

        } catch (error: any) {
          return reject(new Error("[Google Meet BOT Error] " + error.message));
        }
      });

    },
    {
      botConfigData: botConfig,
      selectors: {
        participantSelectors: googleParticipantSelectors,
        speakingClasses: googleSpeakingClassNames,
        silenceClasses: googleSilenceClassNames,
        containerSelectors: googleParticipantContainerSelectors,
        nameSelectors: googleNameSelectors,
        speakingIndicators: googleSpeakingIndicators,
        peopleButtonSelectors: googlePeopleButtonSelectors
      } as any
    }
  );
}
