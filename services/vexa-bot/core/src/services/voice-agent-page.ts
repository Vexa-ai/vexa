import { execSync, spawn, ChildProcess } from "child_process";
import { existsSync } from "fs";
import path from "path";
import { Page } from "playwright-core";
import { googleInitialAdmissionIndicators, googleWaitingRoomIndicators } from "../platforms/googlemeet/selectors";
import { log } from "../utils";
import { ScreenContentService } from "./screen-content";

type OutputDestination = "webcam" | "screenshare";
type SessionDescriptionPayload = { sdp: string; type: string; error?: string };

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class VoiceAgentPageService {
  private readonly parentPage: Page;
  private readonly screenContentService: ScreenContentService;
  private readonly streamerPort: number;
  private readonly streamerDisplay: string;
  private readonly streamerSinkName: string;
  private readonly streamerPulseMonitor: string;
  private readonly streamerVideoFrameSize: string;
  private activeUrl: string | null = null;
  private streamerLoadedUrl: string | null = null;
  private currentOutputDestination: OutputDestination = "webcam";
  private bridgeActive = false;
  private bridgeStarting = false;
  private streamerProcess: ChildProcess | null = null;
  private streamerDisplayProcess: ChildProcess | null = null;
  private keepaliveTimer: NodeJS.Timeout | null = null;
  private audioBridgeModuleId: string | null = null;

  constructor(parentPage: Page, screenContentService: ScreenContentService) {
    this.parentPage = parentPage;
    this.screenContentService = screenContentService;
    this.streamerPort = Number(process.env.VEXA_WEBPAGE_STREAMER_PORT || "8124");
    this.streamerDisplay = process.env.VEXA_WEBPAGE_STREAMER_DISPLAY || ":98";
    this.streamerSinkName = process.env.VEXA_WEBPAGE_STREAMER_SINK || "webpage_streamer_sink";
    this.streamerPulseMonitor =
      process.env.VEXA_WEBPAGE_STREAMER_PULSE_MONITOR || `${this.streamerSinkName}.monitor`;
    this.streamerVideoFrameSize =
      process.env.VEXA_WEBPAGE_STREAMER_VIDEO_SIZE || "1920x1080";
  }

  async setUrl(url: string, outputDestination: OutputDestination = "webcam"): Promise<void> {
    const normalized = (url || "").trim();
    if (!normalized) {
      await this.stop();
      return;
    }

    this.activeUrl = normalized;
    this.currentOutputDestination = outputDestination;

    const initialUrl = this.getPreAdmissionStagingUrl(normalized);
    const sameUrlAlreadyActive = this.streamerLoadedUrl === initialUrl;

    await this.ensureStreamerRuntime();
    await this.ensureStreamerPageLoaded(initialUrl, sameUrlAlreadyActive);
    this.startKeepaliveLoop();

    const connected = await this.activateBridgeIfReady(outputDestination);
    if (connected) {
      await this.ensureActiveAgentSessionReady();
      log(`[VoiceAgentPage] Dedicated webpage streamer active for URL: ${normalized}`);
    } else {
      log(`[VoiceAgentPage] Streamer page loaded and staged for URL: ${initialUrl}; waiting for meeting readiness`);
    }
  }

  async stop(): Promise<void> {
    this.activeUrl = null;
    this.streamerLoadedUrl = null;
    this.bridgeActive = false;
    this.bridgeStarting = false;
    this.stopKeepaliveLoop();

    await this.resetParentPageBridge();
    await this.stopAudioBridge();
    await this.shutdownStreamerRuntime();
    await this.screenContentService.clearScreen();
    log("[VoiceAgentPage] Dedicated webpage streamer stopped");
  }

  async close(): Promise<void> {
    this.activeUrl = null;
    this.streamerLoadedUrl = null;
    this.bridgeActive = false;
    this.bridgeStarting = false;
    this.stopKeepaliveLoop();

    await this.resetParentPageBridge();
    await this.stopAudioBridge();
    await this.shutdownStreamerRuntime();
  }

  async handleMeetingAdmitted(): Promise<void> {
    if (!this.activeUrl) return;

    await this.ensureStreamerRuntime();
    await this.resetParentPageBridge();
    const connected = await this.activateBridgeIfReady(this.currentOutputDestination);
    if (connected) {
      await this.ensureActiveAgentSessionReady();
      log("[VoiceAgentPage] Dedicated streamer bridge refreshed after meeting admission");
    }
  }

  private async ensureStreamerRuntime(): Promise<void> {
    this.ensurePulseSink();
    await this.ensureDisplayServer();
    await this.ensureStreamerProcess();
  }

  private ensurePulseSink(): void {
    try {
      execSync(
        `pactl load-module module-null-sink sink_name=${this.streamerSinkName} sink_properties=device.description=DedicatedWebpageStreamer`,
        { stdio: "pipe" }
      );
    } catch {
      // The sink may already exist; that's fine.
    }
  }

  private async ensureDisplayServer(): Promise<void> {
    try {
      execSync(`xdpyinfo -display ${this.streamerDisplay}`, { stdio: "pipe" });
      return;
    } catch {
      // Need to start the dedicated display.
    }

    if (this.streamerDisplayProcess && this.streamerDisplayProcess.exitCode === null) {
      return;
    }

    const [width, height] = this.parseVideoFrameSize();
    const displayProcess = spawn(
      "Xvfb",
      [this.streamerDisplay, "-screen", "0", `${width}x${height}x24`, "-ac"],
      { stdio: ["ignore", "pipe", "pipe"] }
    );

    displayProcess.stdout?.on("data", (data: Buffer) => {
      const message = data.toString().trim();
      if (message) log(`[VoiceAgentPage][Xvfb] ${message}`);
    });
    displayProcess.stderr?.on("data", (data: Buffer) => {
      const message = data.toString().trim();
      if (message) log(`[VoiceAgentPage][Xvfb] ${message}`);
    });
    displayProcess.on("exit", (code: number | null) => {
      log(`[VoiceAgentPage] Dedicated streamer display exited with code ${code ?? "null"}`);
      if (this.streamerDisplayProcess === displayProcess) {
        this.streamerDisplayProcess = null;
      }
    });

    this.streamerDisplayProcess = displayProcess;

    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        execSync(`xdpyinfo -display ${this.streamerDisplay}`, { stdio: "pipe" });
        return;
      } catch {
        await sleep(250);
      }
    }

    throw new Error(`Dedicated streamer display ${this.streamerDisplay} failed to start`);
  }

  private async ensureStreamerProcess(): Promise<void> {
    if (this.streamerProcess && this.streamerProcess.exitCode === null) {
      await this.waitForStreamerReady();
      return;
    }

    const scriptPath = this.resolveStreamerScriptPath();
    const env = {
      ...process.env,
      DISPLAY: this.streamerDisplay,
      PULSE_SINK: this.streamerSinkName,
      VEXA_WEBPAGE_STREAMER_PULSE_MONITOR: this.streamerPulseMonitor,
    };

    const processHandle = spawn(
      "python3",
      [
        scriptPath,
        "--video-frame-size",
        this.streamerVideoFrameSize,
        "--port",
        String(this.streamerPort),
        "--display",
        this.streamerDisplay,
        "--pulse-monitor",
        this.streamerPulseMonitor,
      ],
      { env, stdio: ["ignore", "pipe", "pipe"] }
    );

    processHandle.stdout?.on("data", (data: Buffer) => {
      const message = data.toString().trim();
      if (message) log(`[VoiceAgentPage][streamer] ${message}`);
    });
    processHandle.stderr?.on("data", (data: Buffer) => {
      const message = data.toString().trim();
      if (message) log(`[VoiceAgentPage][streamer][stderr] ${message}`);
    });
    processHandle.on("exit", (code: number | null) => {
      log(`[VoiceAgentPage] Dedicated webpage streamer exited with code ${code ?? "null"}`);
      if (this.streamerProcess === processHandle) {
        this.streamerProcess = null;
        this.streamerLoadedUrl = null;
        this.bridgeActive = false;
        this.bridgeStarting = false;
      }
    });

    this.streamerProcess = processHandle;
    await this.waitForStreamerReady();
  }

  private async waitForStreamerReady(): Promise<void> {
    for (let attempt = 0; attempt < 120; attempt++) {
      if (this.streamerProcess && this.streamerProcess.exitCode !== null) {
        throw new Error(`Dedicated webpage streamer exited before becoming ready (code ${this.streamerProcess.exitCode})`);
      }
      try {
        await this.postJson("/keepalive", {});
        return;
      } catch {
        await sleep(500);
      }
    }
    throw new Error("Dedicated webpage streamer did not become ready in time");
  }

  private async ensureStreamerPageLoaded(url: string, sameUrlAlreadyActive: boolean): Promise<void> {
    if (sameUrlAlreadyActive) {
      log(`[VoiceAgentPage] Reusing already-loaded dedicated streamer URL: ${url}`);
      return;
    }

    await this.postJson("/start_streaming", { url });
    this.streamerLoadedUrl = url;
    log(`[VoiceAgentPage] Dedicated streamer webpage loaded: ${url}`);
  }

  private async ensureActiveAgentSessionReady(): Promise<void> {
    if (!this.activeUrl) return;
    if (this.streamerLoadedUrl === this.activeUrl) return;

    await this.ensureStreamerPageLoaded(this.activeUrl, false);
    log(`[VoiceAgentPage] Activated voice-agent session with meeting audio ready: ${this.activeUrl}`);
  }

  private getPreAdmissionStagingUrl(url: string): string {
    try {
      const parsed = new URL(url);
      const autostart = (parsed.searchParams.get("autostart") || "").toLowerCase();
      if (autostart !== "true" && autostart !== "1" && autostart !== "yes") {
        return url;
      }
      parsed.searchParams.set("autostart", "false");
      return parsed.toString();
    } catch {
      return url;
    }
  }

  private async activateBridgeIfReady(outputDestination: OutputDestination): Promise<boolean> {
    if (!this.bridgeActive) {
      const connected = await this.connectParentPageToStreamer(outputDestination);
      if (!connected) {
        return false;
      }
    } else {
      await this.playBotOutputMediaStream(outputDestination);
    }

    await this.startAudioBridge();
    return true;
  }

  private async connectParentPageToStreamer(outputDestination: OutputDestination): Promise<boolean> {
    if (this.bridgeActive || this.bridgeStarting) {
      return this.bridgeActive;
    }

    this.bridgeStarting = true;
    try {
      const admitted = await this.isParentMeetingReadyForAudioBridge();
      if (!admitted) {
        return false;
      }

      const offer = await this.parentPage.evaluate(async () => {
        const getter = (window as any).__vexaGetBotOutputPeerConnectionOffer;
        if (typeof getter !== "function") {
          throw new Error("Bot-output offer helper is unavailable on the meeting page");
        }
        return await getter();
      });

      if (!offer) {
        return false;
      }
      if (offer.error) {
        throw new Error(offer.error);
      }

      const answer = await this.postJson("/offer", offer);
      await this.parentPage.evaluate(
        async ({
          remoteAnswer,
          destination,
        }: {
          remoteAnswer: SessionDescriptionPayload;
          destination: OutputDestination;
        }) => {
          const startPeerConnection = (window as any).__vexaStartBotOutputPeerConnection;
          const playBotOutput = (window as any).__vexaPlayBotOutputMediaStream;
          if (typeof startPeerConnection !== "function") {
            throw new Error("Bot-output answer helper is unavailable on the meeting page");
          }
          await startPeerConnection(remoteAnswer);
          if (typeof playBotOutput === "function") {
            await playBotOutput(destination);
          }
        },
        {
          remoteAnswer: answer,
          destination: outputDestination,
        }
      );

      this.bridgeActive = true;
      log("[VoiceAgentPage] Dedicated webpage streamer bridge active");
      return true;
    } catch (err: any) {
      log(`[VoiceAgentPage] Failed to connect dedicated streamer bridge: ${err.message}`);
      return false;
    } finally {
      this.bridgeStarting = false;
    }
  }

  private async playBotOutputMediaStream(outputDestination: OutputDestination): Promise<void> {
    await this.parentPage.evaluate(async (destination: OutputDestination) => {
      const playBotOutput = (window as any).__vexaPlayBotOutputMediaStream;
      if (typeof playBotOutput === "function") {
        await playBotOutput(destination);
      }
    }, outputDestination);
  }

  private startAudioBridge(): Promise<void> {
    if (this.audioBridgeModuleId) return Promise.resolve();

    this.unmuteTtsAudio();
    return new Promise((resolve, reject) => {
      try {
        const moduleId = execSync(
          `pactl load-module module-loopback source=${this.streamerPulseMonitor} sink=tts_sink latency_msec=5 source_dont_move=true sink_dont_move=true`,
          { stdio: ["ignore", "pipe", "pipe"] }
        )
          .toString()
          .trim();
        this.audioBridgeModuleId = moduleId;
        log(`[VoiceAgentPage] Audio bridge started (${this.streamerPulseMonitor} -> tts_sink -> virtual_mic via loopback)`);
        resolve();
      } catch (err: any) {
        void this.stopAudioBridge();
        reject(err);
      }
    });
  }

  private async stopAudioBridge(): Promise<void> {
    if (this.audioBridgeModuleId) {
      try {
        execSync(`pactl unload-module ${this.audioBridgeModuleId}`, { stdio: "pipe" });
      } catch (err: any) {
        log(`[VoiceAgentPage] Unable to unload audio bridge module ${this.audioBridgeModuleId}: ${err.message}`);
      }
      this.audioBridgeModuleId = null;
    }

    this.muteTtsAudio();
  }

  private startKeepaliveLoop(): void {
    this.stopKeepaliveLoop();
    this.keepaliveTimer = setInterval(() => {
      void this.postJson("/keepalive", {}).catch((err) => {
        log(`[VoiceAgentPage] Dedicated streamer keepalive failed: ${err.message}`);
      });
    }, 60000);
  }

  private stopKeepaliveLoop(): void {
    if (this.keepaliveTimer) {
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
    }
  }

  private async resetParentPageBridge(): Promise<void> {
    try {
      if (!this.parentPage.isClosed()) {
        await this.parentPage.evaluate(async () => {
          const stopBotOutput = (window as any).__vexaStopBotOutputMediaStream;
          if (typeof stopBotOutput === "function") {
            await stopBotOutput();
          }

          const closeBotOutputPc = (window as any).__vexaCloseBotOutputPeerConnection;
          if (typeof closeBotOutputPc === "function") {
            closeBotOutputPc();
          }

          const recorder = (window as any).__vexaBotOutputRecorder as MediaRecorder | null;
          if (recorder && recorder.state !== "inactive") {
            recorder.stop();
          }
          const captureIntervalId = (window as any).__vexaBotOutputCaptureIntervalId as number | null;
          if (captureIntervalId) {
            clearInterval(captureIntervalId);
          }
          (window as any).__vexaBotOutputRecorder = null;
          (window as any).__vexaBotOutputCaptureIntervalId = null;
        });
      }
    } catch {
      // Ignore navigation/shutdown races.
    }

    this.bridgeActive = false;
    this.bridgeStarting = false;
  }

  private async shutdownStreamerRuntime(): Promise<void> {
    try {
      await this.postJson("/shutdown", {});
    } catch {
      // If the local HTTP service is already gone, continue with process cleanup.
    }

    if (this.streamerProcess) {
      try {
        this.streamerProcess.kill("SIGTERM");
      } catch {}
      this.streamerProcess = null;
    }
    if (this.streamerDisplayProcess) {
      try {
        this.streamerDisplayProcess.kill("SIGTERM");
      } catch {}
      this.streamerDisplayProcess = null;
    }
  }

  private async postJson<T = any>(endpoint: string, payload: unknown): Promise<T> {
    const response = await fetch(`http://127.0.0.1:${this.streamerPort}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    });

    if (!response.ok) {
      const message = await response.text().catch(() => response.statusText);
      throw new Error(`${endpoint} failed (${response.status}): ${message || response.statusText}`);
    }

    return (await response.json()) as T;
  }

  private parseVideoFrameSize(): [number, number] {
    const [rawWidth, rawHeight] = this.streamerVideoFrameSize.split("x");
    const width = Number(rawWidth) || 1920;
    const height = Number(rawHeight) || 1080;
    return [width, height];
  }

  private resolveStreamerScriptPath(): string {
    const candidates = [
      path.resolve(__dirname, "../../webpage_streamer/run_webpage_streamer.py"),
      path.resolve(__dirname, "../webpage_streamer/run_webpage_streamer.py"),
      path.resolve(process.cwd(), "webpage_streamer/run_webpage_streamer.py"),
    ];
    const match = candidates.find((candidate) => existsSync(candidate));
    if (match) return match;
    throw new Error("Could not locate run_webpage_streamer.py in the bot container");
  }

  private async isParentMeetingReadyForAudioBridge(): Promise<boolean> {
    if (this.parentPage.isClosed()) return false;

    try {
      const parentUrl = this.parentPage.url();
      // Hostname check, not substring: "meet.google.com.evil.example" must not match.
      let parentHost = "";
      try { parentHost = new URL(parentUrl).hostname; } catch { /* about:blank etc. */ }
      if (parentHost !== "meet.google.com") {
        return true;
      }

      return await this.parentPage.evaluate(
        ({
          admissionIndicators,
          waitingIndicators,
        }: {
          admissionIndicators: string[];
          waitingIndicators: string[];
        }) => {
          const isVisible = (selector: string): boolean => {
            if (!selector) return false;

            if (selector.startsWith("text")) {
              const rawText = selector
                .replace(/^text\*?=/, "")
                .replace(/^text\*?/, "")
                .trim()
                .replace(/^["']|["']$/g, "");
              if (!rawText) return false;

              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              let node: Node | null = walker.nextNode();
              while (node) {
                const text = node.textContent?.trim() || "";
                if (text && text.toLowerCase().includes(rawText.toLowerCase())) {
                  const parentEl = node.parentElement;
                  if (parentEl) {
                    const style = window.getComputedStyle(parentEl);
                    if (style.display !== "none" && style.visibility !== "hidden") {
                      return true;
                    }
                  }
                }
                node = walker.nextNode();
              }
              return false;
            }

            const el = document.querySelector(selector) as HTMLElement | null;
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== "none" && style.visibility !== "hidden";
          };

          const inWaitingRoom = waitingIndicators.some(isVisible);
          if (inWaitingRoom) {
            return false;
          }

          return admissionIndicators.some(isVisible);
        },
        {
          admissionIndicators: googleInitialAdmissionIndicators,
          waitingIndicators: googleWaitingRoomIndicators,
        }
      );
    } catch (err: any) {
      log(`[VoiceAgentPage] Admission gate check failed: ${err.message}`);
      return false;
    }
  }

  private unmuteTtsAudio(): void {
    try {
      execSync("pactl set-sink-mute tts_sink 0", { stdio: "pipe" });
      execSync("pactl set-source-mute virtual_mic 0", { stdio: "pipe" });
    } catch (err: any) {
      log(`[VoiceAgentPage] Unable to unmute PulseAudio routing: ${err.message}`);
    }
  }

  private muteTtsAudio(): void {
    try {
      execSync("pactl set-sink-mute tts_sink 1", { stdio: "pipe" });
      execSync("pactl set-source-mute virtual_mic 1", { stdio: "pipe" });
    } catch (err: any) {
      log(`[VoiceAgentPage] Unable to mute PulseAudio routing: ${err.message}`);
    }
  }
}
