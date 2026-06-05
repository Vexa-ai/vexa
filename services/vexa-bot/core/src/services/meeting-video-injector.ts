import { Page } from "playwright-core";
import { log } from "../utils";
import { ScreenContentService } from "./screen-content";

export class MeetingVideoInjectorService {
  private readonly page: Page;
  private readonly screenContentService: ScreenContentService;

  constructor(page: Page, screenContentService: ScreenContentService) {
    this.page = page;
    this.screenContentService = screenContentService;
  }

  async recoverAfterAdmission(): Promise<void> {
    if (this.page.isClosed()) return;

    log("[MeetingVideoInjector] Post-admission: re-enabling virtual camera...");

    try {
      const deepDiag = await this.page.evaluate(() => {
        const win = globalThis as any;
        return {
          canvasExists: !!win.__vexa_canvas,
          canvasStreamExists: !!win.__vexa_canvas_stream,
          gumCallCount: win.__vexa_gum_call_count || 0,
          peerConnections: (win.__vexa_peer_connections || []).length,
          injectedAudioElements: (win.__vexaInjectedAudioElements || []).length,
          hasPreferredVideoTrack: typeof win.__vexaGetPreferredVideoTrack === "function",
        };
      });
      log(`[MeetingVideoInjector] Deep diagnostic: ${JSON.stringify(deepDiag)}`);
    } catch (diagErr: any) {
      log(`[MeetingVideoInjector] Diagnostic error: ${diagErr.message}`);
    }

    const phase1Attempts = 2;
    for (let attempt = 1; attempt <= phase1Attempts; attempt++) {
      try {
        await this.screenContentService.enableCamera();
        await new Promise((resolve) => setTimeout(resolve, 2000));

        const framesSent = await this.checkVideoFramesSent();
        if (framesSent > 0) {
          log(
            `[MeetingVideoInjector] Post-admission video active: framesSent=${framesSent} (phase1, attempt ${attempt})`
          );
          return;
        }

        log(
          `[MeetingVideoInjector] Post-admission framesSent=0 (phase1, attempt ${attempt})`
        );
      } catch (err: any) {
        log(
          `[MeetingVideoInjector] Post-admission camera phase1 attempt ${attempt} failed: ${err.message}`
        );
      }

      if (attempt < phase1Attempts) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
    }

    const preferredTrackState = await this.getPreferredTrackState();
    if (preferredTrackState.hasBotOutputTrack && !preferredTrackState.isCanvasPreferred) {
      log(
        `[MeetingVideoInjector] Active non-canvas preferred track detected (${preferredTrackState.preferredTrackId}); skipping camera toggle fallback`
      );
      await this.runPreferredTrackFallbackLoop();
      return;
    }

    log(
      "[MeetingVideoInjector] Phase 1 failed - attempting camera toggle for SDP renegotiation..."
    );
    const phase2Attempts = 3;
    const phase2Intervals = [3000, 5000, 8000];

    for (let attempt = 1; attempt <= phase2Attempts; attempt++) {
      try {
        const toggled = await this.screenContentService.toggleCameraForRenegotiation();
        if (!toggled) {
          log(
            `[MeetingVideoInjector] Camera toggle attempt ${attempt}: could not find toggle buttons`
          );
          await this.tryPreferredTrackFallback();
          continue;
        }

        await new Promise((resolve) => setTimeout(resolve, 3000));
        const framesSent = await this.checkVideoFramesSent();
        if (framesSent > 0) {
          log(
            `[MeetingVideoInjector] Post-admission video active after toggle: framesSent=${framesSent} (phase2, attempt ${attempt})`
          );
          return;
        }

        log(
          `[MeetingVideoInjector] Post-admission framesSent=0 after toggle (phase2, attempt ${attempt})`
        );

        await this.tryPreferredTrackFallback();
        await new Promise((resolve) => setTimeout(resolve, 1500));

        const fallbackFrames = await this.checkVideoFramesSent();
        if (fallbackFrames > 0) {
          log(
            `[MeetingVideoInjector] Post-admission video active after preferred-track fallback: framesSent=${fallbackFrames} (phase2, attempt ${attempt})`
          );
          return;
        }

        log(
          `[MeetingVideoInjector] Preferred-track fallback still framesSent=0 (phase2, attempt ${attempt})`
        );
      } catch (err: any) {
        log(
          `[MeetingVideoInjector] Post-admission camera phase2 attempt ${attempt} failed: ${err.message}`
        );
      }

      if (attempt < phase2Attempts) {
        await new Promise((resolve) => setTimeout(resolve, phase2Intervals[attempt - 1]));
      }
    }

    log("[MeetingVideoInjector] Post-admission video failed all retries");
  }

  private async runPreferredTrackFallbackLoop(): Promise<void> {
    const attempts = 3;
    const intervals = [3000, 5000, 8000];

    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        await this.tryPreferredTrackFallback();
        await new Promise((resolve) => setTimeout(resolve, 1500));

        const fallbackFrames = await this.checkVideoFramesSent();
        if (fallbackFrames > 0) {
          log(
            `[MeetingVideoInjector] Post-admission video active after preferred-track fallback: framesSent=${fallbackFrames} (attempt ${attempt})`
          );
          return;
        }

        log(
          `[MeetingVideoInjector] Preferred-track fallback still framesSent=0 (attempt ${attempt})`
        );
      } catch (err: any) {
        log(
          `[MeetingVideoInjector] Preferred-track fallback attempt ${attempt} failed: ${err.message}`
        );
      }

      if (attempt < attempts) {
        await new Promise((resolve) => setTimeout(resolve, intervals[attempt - 1]));
      }
    }

    log("[MeetingVideoInjector] Preferred-track fallback failed all retries");
  }

  private async getPreferredTrackState(): Promise<{
    preferredTrackId: string | null;
    preferredTrackReadyState: string | null;
    hasBotOutputTrack: boolean;
    isCanvasPreferred: boolean;
  }> {
    if (this.page.isClosed()) {
      return {
        preferredTrackId: null,
        preferredTrackReadyState: null,
        hasBotOutputTrack: false,
        isCanvasPreferred: false,
      };
    }

    return this.page.evaluate(() => {
      const win = globalThis as any;
      const canvasStream = win.__vexa_canvas_stream as MediaStream | undefined;
      const canvasTrack = canvasStream?.getVideoTracks?.()[0] || null;
      const botOutputStream = win.__vexaBotOutputMediaStream as MediaStream | undefined;
      const botOutputTrack =
        botOutputStream instanceof MediaStream
          ? botOutputStream.getVideoTracks().find((track) => track.readyState === "live") || null
          : null;
      const preferredTrack =
        typeof win.__vexaGetPreferredVideoTrack === "function"
          ? win.__vexaGetPreferredVideoTrack()
          : null;

      return {
        preferredTrackId: preferredTrack?.id || null,
        preferredTrackReadyState: preferredTrack?.readyState || null,
        hasBotOutputTrack: !!botOutputTrack,
        isCanvasPreferred: !!preferredTrack && !!canvasTrack && preferredTrack.id === canvasTrack.id,
      };
    });
  }

  async checkVideoFramesSent(): Promise<number> {
    if (this.page.isClosed()) return 0;

    return this.page.evaluate(async () => {
      const win = globalThis as any;
      const pcs = (win.__vexa_peer_connections || []) as RTCPeerConnection[];
      for (const pc of pcs) {
        if (pc.connectionState === "closed") continue;
        try {
          const stats = await pc.getStats();
          let frames = 0;
          stats.forEach((report: any) => {
            if (report.type === "outbound-rtp" && report.kind === "video") {
              frames = report.framesSent || 0;
            }
          });
          if (frames > 0) return frames;
        } catch {}
      }
      return 0;
    });
  }

  private async tryPreferredTrackFallback(): Promise<void> {
    if (this.page.isClosed()) return;

    log("[MeetingVideoInjector] Trying preferred-track fallback to force video negotiation...");
    try {
      const result = await this.page.evaluate(async () => {
        const win = globalThis as any;
        const pcs = (win.__vexa_peer_connections || []) as RTCPeerConnection[];
        const canvasStream = win.__vexa_canvas_stream as MediaStream | undefined;
        const preferredTrack =
          (typeof win.__vexaGetPreferredVideoTrack === "function"
            ? win.__vexaGetPreferredVideoTrack()
            : null) ||
          canvasStream?.getVideoTracks?.()[0] ||
          null;

        if (!preferredTrack) {
          return { success: false, reason: "no preferred video track" };
        }

        const sourceStream = new MediaStream([preferredTrack]);

        for (const pc of pcs) {
          if (pc.connectionState === "closed") continue;
          const transceivers = pc.getTransceivers();

          for (const t of transceivers) {
            const receiverKind = t.receiver?.track?.kind;
            const senderKind = t.sender?.track?.kind;
            const isVideoTransceiver =
              receiverKind === "video" ||
              senderKind === "video" ||
              (t.sender && !t.sender.track && (t.direction === "sendonly" || t.direction === "sendrecv"));

            if (!isVideoTransceiver || !t.sender) continue;

            try {
              t.direction = "sendrecv";
            } catch {}

            try {
              await t.sender.replaceTrack(preferredTrack);
              return {
                success: true,
                method: "transceiver-replace",
                mid: t.mid,
                pcState: pc.connectionState,
                trackId: preferredTrack.id,
              };
            } catch {}
          }

          try {
            const transceiver = pc.addTransceiver(preferredTrack, { direction: "sendrecv" });
            return {
              success: true,
              method: "addTransceiver",
              mid: transceiver?.mid ?? null,
              pcState: pc.connectionState,
              trackId: preferredTrack.id,
            };
          } catch {}

          try {
            pc.addTrack(preferredTrack, sourceStream);
            return {
              success: true,
              method: "addTrack",
              pcState: pc.connectionState,
              trackId: preferredTrack.id,
            };
          } catch (e) {
            return {
              success: false,
              reason: "addTrack failed: " + ((e as Error)?.message || String(e)),
              trackId: preferredTrack.id,
            };
          }
        }

        return { success: false, reason: "no suitable PC found", trackId: preferredTrack.id };
      });

      log(`[MeetingVideoInjector] Preferred-track fallback result: ${JSON.stringify(result)}`);
    } catch (err: any) {
      log(`[MeetingVideoInjector] Preferred-track fallback error: ${err.message}`);
    }
  }
}
