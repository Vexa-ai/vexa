import { Page } from 'playwright-core';
import { log } from '../utils';
import * as fs from 'fs';
import * as path from 'path';

/** Embedded Vexa logo (dark background, light V) used when vexa-logo-light.png is not found. */
const EMBEDDED_LOGODARK_DATA_URI =
  'data:image/svg+xml;base64,' +
  Buffer.from(
    '<svg width="1030" height="1030" viewBox="0 0 1030 1030" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="3" width="1024" height="1024" rx="225" fill="url(#p0)"/><rect x="3" y="3" width="1024" height="1024" rx="225" stroke="url(#p1)" stroke-opacity="0.4" stroke-width="6" stroke-linejoin="bevel"/><path fill-rule="evenodd" clip-rule="evenodd" d="M657.89 367.455L760.118 308.797C767.782 304.399 777.56 307.047 781.958 314.712L822.422 385.232C826.82 392.897 824.172 402.675 816.507 407.073L714.28 465.73C603.946 529.039 466 451.575 462.625 324.414L459.376 201.997C459.142 193.164 466.113 185.813 474.946 185.579L556.222 183.422C565.056 183.187 572.407 190.158 572.641 198.991L575.89 321.407C576.99 362.842 621.938 388.083 657.89 367.455ZM316.544 465.963L214.632 406.759C206.991 402.321 204.395 392.528 208.834 384.888L249.674 314.585C254.113 306.944 263.905 304.348 271.546 308.787L373.459 367.99C483.452 431.887 485.34 590.083 376.902 656.587L272.511 720.609C264.978 725.229 255.127 722.867 250.507 715.334L208.001 646.026C203.381 638.493 205.742 628.641 213.275 624.022L317.666 560C353 538.33 352.385 486.783 316.544 465.963ZM556.237 845.957C565.073 845.981 572.256 838.837 572.279 830L572.594 712.14C572.705 670.69 617.039 644.384 653.472 664.149L761.112 722.544C768.879 726.757 778.592 723.877 782.806 716.109L821.575 644.644C825.789 636.876 822.908 627.164 815.141 622.95L707.501 564.556C595.688 503.898 459.63 584.63 459.29 711.837L458.975 829.697C458.952 838.534 466.096 845.716 474.932 845.74L556.237 845.957Z" fill="url(#p2)"/><defs><linearGradient id="p0" x1="85" y1="26.5" x2="1027" y2="1027" gradientUnits="userSpaceOnUse"><stop stop-color="#313131"/><stop offset="1" stop-color="#191919"/></linearGradient><linearGradient id="p1" x1="2.99995" y1="-82.5" x2="1593" y2="1538.5" gradientUnits="userSpaceOnUse"><stop stop-color="#595959"/><stop offset="1" stop-color="#5F5F5F"/></linearGradient><linearGradient id="p2" x1="123.627" y1="64.1138" x2="939.893" y2="975.744" gradientUnits="userSpaceOnUse"><stop stop-color="white"/><stop offset="1" stop-color="#B3B3B3"/></linearGradient></defs></svg>'
  ).toString('base64');

/**
 * ScreenContentService
 *
 * Manages a virtual camera feed for the bot by monkey-patching getUserMedia.
 * Instead of using screen share (which doesn't work in Xvfb), we replace
 * the bot's camera feed with a canvas that we can draw images/text onto.
 *
 * How it works:
 * 1. An addInitScript patches navigator.mediaDevices.getUserMedia so that
 *    when Google Meet requests video, it gets a MediaStream from a hidden canvas.
 * 2. The canvas is 1920x1080 and initially shows a black screen.
 * 3. To show an image, we call page.evaluate() to draw it onto the canvas.
 * 4. The canvas.captureStream() automatically updates the video track.
 *
 * This means participants see the bot's "camera" showing our content.
 */
export class ScreenContentService {
  private page: Page;
  private _currentContentType: string | null = null;
  private _currentUrl: string | null = null;
  private _initialized: boolean = false;

  // Default avatar: Vexa logo (small, top-left corner on black background)
  // Can be overridden via setAvatar() API
  private _defaultAvatarDataUri: string | null = null;
  private _customAvatarDataUri: string | null = null;

  constructor(page: Page, defaultAvatarUrl?: string) {
    this.page = page;
    // If a custom default avatar URL was provided via bot config, use it
    if (defaultAvatarUrl) {
      this._customAvatarDataUri = defaultAvatarUrl;
      log(`[ScreenContent] Custom default avatar URL set from config: ${defaultAvatarUrl.substring(0, 80)}...`);
    }
    // Load the built-in Vexa logo as fallback
    this._loadDefaultAvatar();
  }

  private _loadDefaultAvatar(): void {
    try {
      // Try multiple paths (dev vs Docker). The repository currently ships
      // vexa-logo-default.png; keep vexa-logo-light.png as a legacy fallback.
      const possiblePaths = [
        path.join(__dirname, '../../assets/vexa-logo-default.png'),
        path.join(__dirname, '../assets/vexa-logo-default.png'),
        path.join(__dirname, '../../assets/vexa-logo-light.png'),
        path.join(__dirname, '../assets/vexa-logo-light.png'),
        '/app/assets/vexa-logo-default.png',
        '/app/assets/vexa-logo-light.png',
      ];
      for (const p of possiblePaths) {
        if (fs.existsSync(p)) {
          const buf = fs.readFileSync(p);
          this._defaultAvatarDataUri = `data:image/png;base64,${buf.toString('base64')}`;
          log(`[ScreenContent] Default avatar loaded from ${p} (${buf.length} bytes)`);
          return;
        }
      }
      // No PNG found: use embedded Vexa logo (dark background, light V) so we never show the text placeholder
      this._defaultAvatarDataUri = EMBEDDED_LOGODARK_DATA_URI;
      log('[ScreenContent] Default avatar: using embedded Vexa logo (no logo PNG found on disk)');
    } catch (err: any) {
      log(`[ScreenContent] Failed to load default avatar: ${err.message}`);
      this._defaultAvatarDataUri = EMBEDDED_LOGODARK_DATA_URI;
    }
  }

  /**
   * Get the current avatar data URI (custom or default).
   */
  private _getAvatarDataUri(): string | null {
    return this._customAvatarDataUri || this._defaultAvatarDataUri;
  }

  /**
   * Initialize the virtual canvas camera.
   * Must be called AFTER the page has navigated to Google Meet.
   * The canvas and stream are already created by the init script — this
   * just verifies they exist and are usable.
   */
  async initialize(): Promise<void> {
    if (this._initialized) return;

    // The init script (getVirtualCameraInitScript) already created the canvas,
    // ctx, and stream. Verify they're present.
    const status = await this.page.evaluate(() => {
      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      const stream = (window as any).__vexa_canvas_stream as MediaStream;
      return {
        hasCanvas: !!canvas,
        hasCtx: !!ctx,
        hasStream: !!stream,
        videoTracks: stream ? stream.getVideoTracks().length : 0,
      };
    });

    if (!status.hasCanvas || !status.hasCtx || !status.hasStream) {
      // Init script didn't run yet or failed — create canvas now as fallback
      log('[ScreenContent] Init script canvas not found, creating fallback canvas...');
      await this.page.evaluate(() => {
        if ((window as any).__vexa_canvas) return; // already exists

        const canvas = document.createElement('canvas');
        canvas.id = '__vexa_screen_canvas';
        canvas.width = 1920;
        canvas.height = 1080;
        canvas.style.position = 'fixed';
        canvas.style.top = '-9999px';
        canvas.style.left = '-9999px';
        document.body.appendChild(canvas);

        const ctx = canvas.getContext('2d')!;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, 1920, 1080);

        const stream = canvas.captureStream(30);

        (window as any).__vexa_canvas = canvas;
        (window as any).__vexa_canvas_ctx = ctx;
        (window as any).__vexa_canvas_stream = stream;
      });
    }

    this._initialized = true;
    log(`[ScreenContent] Canvas virtual camera initialized (initScript canvas: ${status.hasCanvas}, tracks: ${status.videoTracks})`);

    // Draw the default avatar on the canvas (replaces the init script placeholder)
    const avatarUri = this._getAvatarDataUri();
    if (avatarUri) {
      await this._drawAvatarOnCanvas(avatarUri);
      log('[ScreenContent] Default avatar drawn on canvas');
    }

    // Start the frame pump — captureStream(30) only emits frames when the
    // canvas changes. This loop makes an invisible 1-pixel change on every
    // animation frame to keep the stream alive.
    await this._startFramePump();
  }

  /**
   * Start a requestAnimationFrame loop that touches a single pixel on
   * each frame, forcing captureStream(30) to continuously emit frames.
   * Without this, static content (avatar, images) produces only 1-2 frames
   * and Google Meet stops displaying the video feed.
   */
  private async _startFramePump(): Promise<void> {
    await this.page.evaluate(() => {
      // Don't start twice
      if ((window as any).__vexa_frame_pump_active) return;
      (window as any).__vexa_frame_pump_active = true;

      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      if (!canvas || !ctx) return;

      // Continuously "touch" the canvas to force captureStream to emit frames.
      // We read+write a single pixel at (0,0) — this triggers a change event
      // without any visible effect on the content.
      let toggle = false;
      const pump = () => {
        if (!(window as any).__vexa_frame_pump_active) return;

        // Alternate between two invisible operations to ensure the canvas
        // is always "dirty" from captureStream's perspective.
        try {
          const pixel = ctx.getImageData(0, 0, 1, 1);
          // Flip the alpha by 1 unit (invisible at these values)
          pixel.data[3] = toggle ? 254 : 255;
          toggle = !toggle;
          ctx.putImageData(pixel, 0, 0);
        } catch {}

        requestAnimationFrame(pump);
      };
      requestAnimationFrame(pump);
      console.log('[Vexa] Frame pump started for continuous captureStream output');
    });
    log('[ScreenContent] Frame pump started');
  }

  /**
   * Teams light-meetings may expose only "Open video options". Try selecting
   * a camera device there, then use the Teams keyboard shortcut for video.
   */
  private async tryTeamsVideoOptionsFallback(): Promise<boolean> {
    const videoOptionsBtn = this.page.locator([
      'button[aria-label="Open video options"]',
      'button[aria-label="open video options"]',
      'button[aria-label="Video options"]',
      'button[aria-label="video options"]',
      'button[aria-label="Camera options"]',
      'button[aria-label="camera options"]',
      'button:has-text("Open video options")',
    ].join(', ')).first();

    const optionsVisible = await videoOptionsBtn.isVisible().catch(() => false);
    if (!optionsVisible) return false;

    try {
      const label = await videoOptionsBtn.getAttribute('aria-label');
      await videoOptionsBtn.click({ force: true });
      log(`[ScreenContent] Opened video options${label ? ` ("${label}")` : ''}`);
      await this.page.waitForTimeout(700);
    } catch (err: any) {
      log(`[ScreenContent] Failed to open video options: ${err.message}`);
      return false;
    }

    let deviceSelected = false;

    const vexaOption = this.page.locator([
      '[role="menuitemradio"]:has-text("Vexa Virtual Camera")',
      '[role="option"]:has-text("Vexa Virtual Camera")',
      'button:has-text("Vexa Virtual Camera")',
      '[data-tid*="camera"]:has-text("Vexa Virtual Camera")',
      'span:has-text("Vexa Virtual Camera")',
    ].join(', ')).first();
    try {
      const vexaVisible = await vexaOption.isVisible().catch(() => false);
      if (vexaVisible) {
        await vexaOption.click({ force: true });
        deviceSelected = true;
        log('[ScreenContent] Selected "Vexa Virtual Camera" in video options');
      }
    } catch {}

    if (!deviceSelected) {
      const fallbackCameraLabel = await this.page.evaluate(() => {
        const normalize = (value: string | null | undefined): string =>
          (value || '').replace(/\s+/g, ' ').trim();
        const isVisible = (el: Element): boolean => {
          const node = el as HTMLElement;
          const style = window.getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return (
            rect.width > 0 &&
            rect.height > 0 &&
            style.visibility !== 'hidden' &&
            style.display !== 'none'
          );
        };
        const candidates = Array.from(
          document.querySelectorAll('[role="menuitemradio"], [role="option"], button, [data-tid], [aria-label]')
        );
        for (const el of candidates) {
          if (!isVisible(el)) continue;
          const label = normalize((el as HTMLElement).innerText || el.getAttribute('aria-label'));
          if (!label) continue;
          const lower = label.toLowerCase();
          const isCameraDeviceCandidate =
            lower.includes('camera') &&
            !lower.includes('open video options') &&
            !lower.includes('video options') &&
            !lower.includes('turn on camera') &&
            !lower.includes('turn off camera') &&
            !lower.includes('turn camera on') &&
            !lower.includes('turn camera off') &&
            !lower.includes('turn on video') &&
            !lower.includes('turn off video') &&
            !lower.includes('no camera');
          if (!isCameraDeviceCandidate) continue;
          (el as HTMLElement).click();
          return label;
        }
        return null;
      });

      if (fallbackCameraLabel) {
        deviceSelected = true;
        log(`[ScreenContent] Selected fallback camera option: "${fallbackCameraLabel}"`);
      } else {
        log('[ScreenContent] No selectable camera device found in video options');
      }
    }

    await this.page.keyboard.press('Escape').catch(() => {});
    await this.page.waitForTimeout(500);

    await this.page.keyboard.press('Control+Shift+O').catch(() => {});
    await this.page.waitForTimeout(1000);

    const turnOffVisible = await this.page.locator([
      'button[aria-label="Turn off camera"]',
      'button[aria-label="turn off camera"]',
      'button[aria-label="Turn camera off"]',
      'button[aria-label="turn camera off"]',
      'button[aria-label="Turn off video"]',
      'button[aria-label="turn off video"]',
    ].join(', ')).first().isVisible().catch(() => false);

    if (turnOffVisible) {
      log('[ScreenContent] Video options fallback succeeded; camera appears ON');
      return true;
    }

    log('[ScreenContent] Video options fallback did not expose a camera-ON state');
    return deviceSelected;
  }

  /**
   * Turn on the camera/video button if it's off.
   * Works for both Google Meet ("Turn on camera") and Teams ("Turn on video").
   * The getUserMedia patch ensures that when the platform gets the camera stream,
   * it receives our canvas stream. So just clicking the button is enough.
   */
  async enableCamera(): Promise<void> {
    if (!this._initialized) await this.initialize();

    // First, log all toolbar buttons for diagnostics
    const toolbarButtons = await this.page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      return buttons
        .filter(b => {
          const rect = b.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })
        .map(b => ({
          ariaLabel: b.getAttribute('aria-label') || '',
          tooltip: b.getAttribute('data-tooltip') || '',
        }))
        .filter(b =>
          b.ariaLabel.toLowerCase().includes('camera') ||
          b.ariaLabel.toLowerCase().includes('video') ||
          b.ariaLabel.toLowerCase().includes('камер') ||
          b.tooltip.toLowerCase().includes('camera') ||
          b.tooltip.toLowerCase().includes('video')
        );
    });
    log(`[ScreenContent] Camera-related buttons: ${JSON.stringify(toolbarButtons)}`);

    // Click "Turn on camera/video" if it's visible (means camera is currently off)
    // Includes both Google Meet ("camera") and Teams ("video"/"camera") selectors
    // NOTE: Teams has "Open video options" which also contains "video" — we must
    // use specific prefixes to avoid matching the wrong button.
    // Teams uses BOTH "Turn camera on" and "Turn on camera" depending on version.
    const turnOnCameraBtn = this.page.locator([
      // Google Meet selectors
      'button[aria-label="Turn on camera"]',
      'button[aria-label="turn on camera"]',
      'button[aria-label="Включить камеру"]',
      'button[data-tooltip="Turn on camera"]',
      // Teams selectors — multiple aria-label variants
      'button[aria-label="Turn on video"]',
      'button[aria-label="turn on video"]',
      'button[aria-label="Turn camera on"]',
      'button[aria-label="turn camera on"]',
    ].join(', ')).first();

    try {
      await turnOnCameraBtn.waitFor({ state: 'visible', timeout: 5000 });
      const label = await turnOnCameraBtn.getAttribute('aria-label');
      log(`[ScreenContent] Found camera/video button: "${label}", clicking...`);
      await turnOnCameraBtn.click({ force: true });
      log('[ScreenContent] Clicked camera/video button — getUserMedia patch will provide canvas stream');
      // Wait for camera to initialize and getUserMedia to be called
      await this.page.waitForTimeout(3000);
    } catch {
      log('[ScreenContent] Camera/video button not found — trying "Turn off" check (maybe already on)');
      // Check if camera is already on
      const turnOffCameraBtn = this.page.locator([
        'button[aria-label="Turn off camera"]',
        'button[aria-label="turn off camera"]',
        'button[aria-label="Выключить камеру"]',
        // Teams — multiple variants
        'button[aria-label="Turn off video"]',
        'button[aria-label="turn off video"]',
        'button[aria-label="Turn camera off"]',
        'button[aria-label="turn camera off"]',
      ].join(', ')).first();
      try {
        await turnOffCameraBtn.waitFor({ state: 'visible', timeout: 2000 });
        log('[ScreenContent] Camera/video is already ON (found "Turn off" button)');
      } catch {
        log('[ScreenContent] Neither camera/video on nor off button found — trying video options fallback');
        const fallbackEnabled = await this.tryTeamsVideoOptionsFallback();
        if (!fallbackEnabled) {
          log('[ScreenContent] Video options fallback unavailable or unsuccessful');
        }
      }
    }

    // Diagnostic: check if our canvas track is being sent via WebRTC
    const diagnostic = await this.page.evaluate(() => {
      const pcs = (window as any).__vexa_peer_connections as RTCPeerConnection[] || [];
      const canvasStream = (window as any).__vexa_canvas_stream as MediaStream;
      const canvasTrackId = canvasStream?.getVideoTracks()[0]?.id || 'none';
      const info: any[] = [];

      for (let i = 0; i < pcs.length; i++) {
        const pc = pcs[i];
        if (pc.connectionState === 'closed') continue;
        const senders = pc.getSenders();
        for (const s of senders) {
          if (s.track && s.track.kind === 'video') {
            info.push({
              pc: i,
              trackId: s.track.id,
              isCanvasTrack: s.track.id === canvasTrackId,
              trackLabel: s.track.label,
              enabled: s.track.enabled,
              readyState: s.track.readyState,
            });
          }
        }
      }

      // Also check transceivers for video slots
      const transceiverInfo: any[] = [];
      for (let i = 0; i < pcs.length; i++) {
        const pc = pcs[i];
        if (pc.connectionState === 'closed') continue;
        try {
          for (const t of pc.getTransceivers()) {
            if (t.sender && (
              t.receiver?.track?.kind === 'video' ||
              (t.sender.track && t.sender.track.kind === 'video') ||
              (t.mid && t.mid.includes('video'))
            )) {
              transceiverInfo.push({
                pc: i,
                mid: t.mid,
                senderTrackId: t.sender.track?.id || 'null',
                isCanvasTrack: t.sender.track?.id === canvasTrackId,
                direction: t.direction,
              });
            }
          }
        } catch {}
      }

      return {
        canvasTrackId,
        peerConnections: pcs.length,
        videoSenders: info,
        videoTransceivers: transceiverInfo,
        gumCallCount: (window as any).__vexa_gum_call_count || 0,
        gumVideoIntercepted: (window as any).__vexa_gum_video_intercepted || 0,
        addTrackIntercepted: (window as any).__vexa_addtrack_intercepted || 0,
      };
    });
    log(`[ScreenContent] Camera diagnostic: ${JSON.stringify(diagnostic)}`);

    // Always try replaceTrack to ensure our canvas is the active video source.
    // --use-fake-ui-for-media-stream bypasses our getUserMedia JS patch, so
    // Chromium provides fake device video at a lower level. We need replaceTrack
    // to swap the fake/null track for our canvas track.
    log('[ScreenContent] Attempting replaceTrack to inject canvas stream into WebRTC...');
    const replaceResult = await this.page.evaluate(async () => {
      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      if (!canvas) return { success: false, reason: 'no canvas' };

      // "Touch" the canvas to force captureStream to generate a new frame.
      // captureStream(30) only emits frames when canvas content changes.
      // Drawing a tiny invisible pixel forces a change event.
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      if (ctx) {
        // Read then write a single pixel at (0,0) — triggers frame without visual change
        const pixel = ctx.getImageData(0, 0, 1, 1);
        ctx.putImageData(pixel, 0, 0);
      }

      // Always create a fresh captureStream to get a live track.
      // Google Meet's camera toggle can kill previous tracks.
      const freshStream = canvas.captureStream(30);
      (window as any).__vexa_canvas_stream = freshStream;
      const canvasTrack = freshStream.getVideoTracks()[0];
      if (!canvasTrack) return { success: false, reason: 'failed to get canvas track from fresh stream' };
      console.log('[Vexa] Fresh canvas track created: id=' + canvasTrack.id + ' readyState=' + canvasTrack.readyState);

      const pcs = (window as any).__vexa_peer_connections as RTCPeerConnection[] || [];
      let replaced = 0;
      const details: string[] = [];
      const errors: string[] = [];

      for (let i = 0; i < pcs.length; i++) {
        const pc = pcs[i];
        if (pc.connectionState === 'closed') continue;
        try {
          const transceivers = pc.getTransceivers();
          for (const t of transceivers) {
            // Only replace on sendonly or sendrecv transceivers with video capability
            const isSendVideo =
              (t.direction === 'sendonly' || t.direction === 'sendrecv') &&
              (t.sender !== null) &&
              // Check if this transceiver handles video
              (t.receiver?.track?.kind === 'video' ||
               (t.sender.track && t.sender.track.kind === 'video') ||
               // Also match transceivers with null sender track (camera off/fake device)
               (t.sender.track === null && t.direction === 'sendonly'));

            if (isSendVideo) {
              try {
                await t.sender.replaceTrack(canvasTrack);
                replaced++;
                details.push('pc' + i + ':mid=' + t.mid + ':dir=' + t.direction);
              } catch (e: any) {
                errors.push('pc' + i + ':mid=' + t.mid + ':' + e.message);
              }
            }
          }
        } catch (e: any) {
          errors.push('pc' + i + ':getTransceivers:' + e.message);
        }

        // Fallback: also try senders directly
        if (replaced === 0) {
          const senders = pc.getSenders();
          for (const s of senders) {
            if (s.track === null || (s.track && s.track.kind === 'video')) {
              try {
                await s.replaceTrack(canvasTrack);
                replaced++;
                details.push('pc' + i + ':sender(trackWas=' + (s.track?.kind || 'null') + ')');
              } catch (e: any) {
                errors.push('pc' + i + ':sender:' + e.message);
              }
            }
          }
        }
      }

      // Verify the replacement
      const verification: any[] = [];
      for (let i = 0; i < pcs.length; i++) {
        const pc = pcs[i];
        if (pc.connectionState === 'closed') continue;
        for (const s of pc.getSenders()) {
          if (s.track && s.track.kind === 'video') {
            verification.push({
              pc: i,
              trackId: s.track.id,
              isCanvas: s.track.id === canvasTrack.id,
              label: s.track.label,
              enabled: s.track.enabled,
              readyState: s.track.readyState,
            });
          }
        }
      }

      return {
        success: replaced > 0,
        replaced,
        details: details.join(', '),
        errors: errors.length > 0 ? errors.join(', ') : undefined,
        verification,
      };
    });
    log(`[ScreenContent] replaceTrack result: ${JSON.stringify(replaceResult)}`);

    // Deep WebRTC SDP diagnostic — check what the SDP says about video
    const sdpDiag = await this.page.evaluate(async () => {
      const pcs = (window as any).__vexa_peer_connections as RTCPeerConnection[] || [];
      const results: any[] = [];
      for (let i = 0; i < pcs.length; i++) {
        const pc = pcs[i];
        if (pc.connectionState === 'closed') continue;
        const localDesc = pc.localDescription;
        const remoteDesc = pc.remoteDescription;

        // Parse video m= lines from SDP
        const parseVideoLines = (sdp: string | null | undefined) => {
          if (!sdp) return null;
          const lines = sdp.split('\n');
          const videoSections: string[] = [];
          let inVideo = false;
          let current = '';
          for (const line of lines) {
            if (line.startsWith('m=video')) {
              inVideo = true;
              current = line.trim();
            } else if (line.startsWith('m=') && inVideo) {
              videoSections.push(current);
              inVideo = false;
              current = '';
            } else if (inVideo) {
              // Only capture key lines: a=mid, a=sendonly, a=recvonly, a=inactive, a=msid, a=ssrc
              const trimmed = line.trim();
              if (trimmed.startsWith('a=mid:') || trimmed.startsWith('a=sendonly') ||
                  trimmed.startsWith('a=recvonly') || trimmed.startsWith('a=inactive') ||
                  trimmed.startsWith('a=sendrecv') || trimmed.startsWith('a=msid:') ||
                  trimmed.startsWith('a=ssrc:') || trimmed.startsWith('a=extmap-allow-mixed') ||
                  trimmed.startsWith('c=')) {
                current += ' | ' + trimmed;
              }
            }
          }
          if (current) videoSections.push(current);
          return videoSections;
        };

        // Also get sender track info AFTER replaceTrack
        const senderInfo: any[] = [];
        for (const s of pc.getSenders()) {
          senderInfo.push({
            trackKind: s.track?.kind || 'null',
            trackId: s.track?.id?.substring(0, 16) || 'null',
            trackLabel: s.track?.label?.substring(0, 40) || 'null',
            trackReadyState: s.track?.readyState || 'null',
            trackEnabled: s.track?.enabled ?? null,
          });
        }

        // getStats for outbound video
        let outboundVideoStats: any = null;
        try {
          const stats = await pc.getStats();
          stats.forEach((report: any) => {
            if (report.type === 'outbound-rtp' && report.kind === 'video') {
              outboundVideoStats = {
                bytesSent: report.bytesSent,
                packetsSent: report.packetsSent,
                framesSent: report.framesSent,
                framesEncoded: report.framesEncoded,
                frameWidth: report.frameWidth,
                frameHeight: report.frameHeight,
                framesPerSecond: report.framesPerSecond,
                qualityLimitationReason: report.qualityLimitationReason,
                active: report.active,
              };
            }
          });
        } catch {}

        results.push({
          pc: i,
          connectionState: pc.connectionState,
          iceConnectionState: pc.iceConnectionState,
          signalingState: pc.signalingState,
          localVideoSDP: parseVideoLines(localDesc?.sdp),
          remoteVideoSDP: parseVideoLines(remoteDesc?.sdp),
          senders: senderInfo,
          outboundVideoStats,
        });
      }
      return results;
    });
    log(`[ScreenContent] SDP diagnostic: ${JSON.stringify(sdpDiag)}`);
  }

  /**
   * Toggle camera off→on to force Teams SDP renegotiation.
   *
   * Teams "light meetings" (anonymous/guest) sometimes sets video to `inactive`
   * in the initial SDP answer, making all replaceTrack attempts useless.
   * Toggling the camera UI off then on forces Teams to renegotiate the SDP
   * with video enabled, allowing the virtual camera stream to flow.
   */
  async toggleCameraForRenegotiation(): Promise<boolean> {
    log('[ScreenContent] Attempting camera toggle (off→on) to force SDP renegotiation...');

    // All known "camera/video off" button selectors for Teams + Meet
    const turnOffSelectors = [
      'button[aria-label="Turn off camera"]',
      'button[aria-label="turn off camera"]',
      'button[aria-label="Turn camera off"]',
      'button[aria-label="turn camera off"]',
      'button[aria-label="Turn off video"]',
      'button[aria-label="turn off video"]',
      'button[aria-label="Выключить камеру"]',
    ];

    const turnOnSelectors = [
      'button[aria-label="Turn on camera"]',
      'button[aria-label="turn on camera"]',
      'button[aria-label="Turn camera on"]',
      'button[aria-label="turn camera on"]',
      'button[aria-label="Turn on video"]',
      'button[aria-label="turn on video"]',
      'button[aria-label="Включить камеру"]',
    ];

    try {
      // Step 1: Turn camera OFF
      const turnOffBtn = this.page.locator(turnOffSelectors.join(', ')).first();
      try {
        await turnOffBtn.waitFor({ state: 'visible', timeout: 3000 });
        const label = await turnOffBtn.getAttribute('aria-label');
        log(`[ScreenContent] Toggle: clicking OFF button ("${label}")...`);
        await turnOffBtn.click({ force: true });
        // Wait for Teams to process the camera-off and release the video track
        await this.page.waitForTimeout(2000);
      } catch {
        // Camera might already be off — try turning it on directly
        log('[ScreenContent] Toggle: no "turn off" button found — camera may already be off');
      }

      // Step 2: Turn camera ON (this triggers getUserMedia → our canvas track → SDP renegotiation)
      const turnOnBtn = this.page.locator(turnOnSelectors.join(', ')).first();
      try {
        await turnOnBtn.waitFor({ state: 'visible', timeout: 3000 });
        const label = await turnOnBtn.getAttribute('aria-label');
        log(`[ScreenContent] Toggle: clicking ON button ("${label}")...`);
        await turnOnBtn.click({ force: true });
        // Wait for getUserMedia, replaceTrack, and SDP renegotiation
        await this.page.waitForTimeout(3000);
        log('[ScreenContent] Toggle: camera toggled on — SDP renegotiation should be in progress');

        // Now run replaceTrack to ensure our canvas stream is the active video source
        await this.enableCamera();
        return true;
      } catch {
        log('[ScreenContent] Toggle: no "turn on" button found — trying video options fallback');
        const fallbackEnabled = await this.tryTeamsVideoOptionsFallback();
        if (fallbackEnabled) {
          await this.enableCamera();
          return true;
        }
        log('[ScreenContent] Toggle: video options fallback failed');
        return false;
      }
    } catch (err: any) {
      log(`[ScreenContent] Toggle failed: ${err.message}`);
      return false;
    }
  }

  /**
   * Display an image on the virtual camera feed.
   * @param imageSource URL or base64 data URI for the image
   */
  async showImage(imageSource: string): Promise<void> {
    if (!this._initialized) await this.initialize();

    // Handle base64 images
    let src = imageSource;
    if (!imageSource.startsWith('http') && !imageSource.startsWith('data:')) {
      src = `data:image/png;base64,${imageSource}`;
    }

    // Draw the image onto the canvas
    const success = await this.page.evaluate(async (imgSrc: string) => {
      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      if (!canvas || !ctx) return false;

      return new Promise<boolean>((resolve) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          // Clear canvas to black
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, canvas.width, canvas.height);

          // Calculate centered fit (contain)
          const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
          const w = img.width * scale;
          const h = img.height * scale;
          const x = (canvas.width - w) / 2;
          const y = (canvas.height - h) / 2;

          ctx.drawImage(img, x, y, w, h);
          resolve(true);
        };
        img.onerror = () => {
          // Draw error text
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = '#ff0000';
          ctx.font = '48px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('Failed to load image', canvas.width / 2, canvas.height / 2);
          resolve(false);
        };
        img.src = imgSrc;
      });
    }, src);

    if (success) {
      this._currentContentType = 'image';
      this._currentUrl = imageSource;
      log(`[ScreenContent] Showing image on virtual camera: ${imageSource.substring(0, 80)}...`);
    } else {
      log(`[ScreenContent] Failed to load image: ${imageSource.substring(0, 80)}...`);
    }

    // Enable camera if not already
    await this.enableCamera();
  }

  /**
   * Display custom HTML-rendered content.
   * For now, just show text on the canvas.
   */
  async showText(text: string, fontSize: number = 48): Promise<void> {
    if (!this._initialized) await this.initialize();

    await this.page.evaluate(({ text, fontSize }: { text: string; fontSize: number }) => {
      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      if (!canvas || !ctx) return;

      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.fillStyle = '#ffffff';
      ctx.font = `${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      // Word wrap
      const maxWidth = canvas.width - 100;
      const words = text.split(' ');
      const lines: string[] = [];
      let currentLine = words[0];

      for (let i = 1; i < words.length; i++) {
        const testLine = currentLine + ' ' + words[i];
        const metrics = ctx.measureText(testLine);
        if (metrics.width > maxWidth) {
          lines.push(currentLine);
          currentLine = words[i];
        } else {
          currentLine = testLine;
        }
      }
      lines.push(currentLine);

      const lineHeight = fontSize * 1.3;
      const totalHeight = lines.length * lineHeight;
      const startY = (canvas.height - totalHeight) / 2 + fontSize / 2;

      for (let i = 0; i < lines.length; i++) {
        ctx.fillText(lines[i], canvas.width / 2, startY + i * lineHeight);
      }
    }, { text, fontSize });

    this._currentContentType = 'text';
    this._currentUrl = null;
    log(`[ScreenContent] Showing text on virtual camera: "${text.substring(0, 50)}..."`);

    await this.enableCamera();
  }

  /**
   * Clear the canvas — reverts to showing the default avatar (Vexa logo).
   * If no avatar is available, shows black.
   */
  async clearScreen(): Promise<void> {
    if (!this._initialized) return;

    // Try to show the avatar instead of a plain black screen
    const avatarUri = this._getAvatarDataUri();
    if (avatarUri) {
      await this._drawAvatarOnCanvas(avatarUri);
    } else {
      await this.page.evaluate(() => {
        const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
        const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
        if (!canvas || !ctx) return;

        // Dark branded background when no avatar is available
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      });
    }

    this._currentContentType = null;
    this._currentUrl = null;
    log('[ScreenContent] Screen cleared (showing default avatar)');
  }

  /**
   * Set a custom avatar image (replaces the default Vexa logo).
   * @param imageSource URL or base64 data URI of the avatar image
   */
  async setAvatar(imageSource: string): Promise<void> {
    let src = imageSource;
    if (!imageSource.startsWith('http') && !imageSource.startsWith('data:')) {
      src = `data:image/png;base64,${imageSource}`;
    }
    this._customAvatarDataUri = src;
    log(`[ScreenContent] Custom avatar set: ${src.substring(0, 60)}...`);

    // If currently showing avatar (no active content), refresh the display
    if (!this._currentContentType && this._initialized) {
      await this._drawAvatarOnCanvas(src);
    }
  }

  /**
   * Reset avatar to the default Vexa logo.
   */
  async resetAvatar(): Promise<void> {
    this._customAvatarDataUri = null;
    log('[ScreenContent] Avatar reset to default');

    // If currently showing avatar (no active content), refresh the display
    if (!this._currentContentType && this._initialized) {
      const avatarUri = this._getAvatarDataUri();
      if (avatarUri) {
        await this._drawAvatarOnCanvas(avatarUri);
      }
    }
  }

  /**
   * Draw an avatar image centered on a black background.
   * The logo is drawn small (~12% of canvas height) and centered.
   */
  private async _drawAvatarOnCanvas(avatarUri: string): Promise<void> {
    await this.page.evaluate(async (imgSrc: string) => {
      const canvas = (window as any).__vexa_canvas as HTMLCanvasElement;
      const ctx = (window as any).__vexa_canvas_ctx as CanvasRenderingContext2D;
      if (!canvas || !ctx) return;

      return new Promise<void>((resolve) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          // Black background
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, canvas.width, canvas.height);

          // Draw the logo small and centered (~12% of canvas height)
          const maxSize = Math.max(Math.round(canvas.height * 0.12), 100);
          const scale = Math.min(maxSize / img.width, maxSize / img.height);
          const w = img.width * scale;
          const h = img.height * scale;
          const x = (canvas.width - w) / 2;
          const y = (canvas.height - h) / 2;

          ctx.drawImage(img, x, y, w, h);
          resolve();
        };
        img.onerror = () => {
          // Image failed to load: show black only (never show text placeholder)
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          resolve();
        };
        img.src = imgSrc;
      });
    }, avatarUri);
  }

  /**
   * Close / cleanup.
   */
  async close(): Promise<void> {
    this._currentContentType = null;
    this._currentUrl = null;
    this._initialized = false;
    log('[ScreenContent] Content service closed');
  }

  /**
   * Get current display status.
   */
  getStatus(): { hasContent: boolean; contentType: string | null; url: string | null } {
    return {
      hasContent: this._currentContentType !== null,
      contentType: this._currentContentType,
      url: this._currentUrl
    };
  }
}

/**
 * Get the addInitScript code that monkey-patches getUserMedia and RTCPeerConnection.
 * This MUST be injected BEFORE the page navigates to Google Meet.
 *
 * It intercepts:
 * 1. getUserMedia — when video is requested, returns a canvas-based stream
 *    instead of the real camera, so Google Meet uses our canvas from the start.
 * 2. RTCPeerConnection — tracks all connections so we can inspect video senders.
 *
 * The canvas is created eagerly (before getUserMedia is called) and shared
 * between the init script and ScreenContentService.
 */
export function getVirtualCameraInitScript(): string {
  return `
    (() => {
      console.log('[Vexa] Virtual camera init script START in: ' + window.location.href);
      try {
      // ===== 1. Create the canvas and stream eagerly =====
      // We create a 1920x1080 canvas and captureStream(30) immediately.
      // ScreenContentService.initialize() will find these globals and reuse them.
      const canvas = document.createElement('canvas');
      canvas.id = '__vexa_screen_canvas';
      canvas.width = 1920;
      canvas.height = 1080;
      canvas.style.position = 'fixed';
      canvas.style.top = '-9999px';
      canvas.style.left = '-9999px';

      const ctx = canvas.getContext('2d');
      if (ctx) {
        // Initial frame: black only. ScreenContentService.initialize() will draw the logo.
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, 1920, 1080);
      }

      const canvasStream = canvas.captureStream(30);

      // Store globally for ScreenContentService to use
      window.__vexa_canvas = canvas;
      window.__vexa_canvas_ctx = ctx;
      window.__vexa_canvas_stream = canvasStream;

      // Counters for diagnostics
      window.__vexa_gum_call_count = 0;
      window.__vexa_gum_video_intercepted = 0;

      // Append canvas to body when DOM is ready
      const appendCanvas = () => {
        if (document.body) {
          document.body.appendChild(canvas);
        } else {
          document.addEventListener('DOMContentLoaded', () => {
            document.body.appendChild(canvas);
          });
        }
      };
      appendCanvas();

      // ===== 2. Patch getUserMedia =====
      // When Google Meet calls getUserMedia({video: true, audio: true}),
      // we return our canvas video track + the real audio track.
      // This means Meet uses our canvas as the "camera" from the very start.
      const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

      navigator.mediaDevices.getUserMedia = async function(constraints) {
        window.__vexa_gum_call_count = (window.__vexa_gum_call_count || 0) + 1;
        console.log('[Vexa] getUserMedia called with:', JSON.stringify(constraints));

        const wantsVideo = !!(constraints && constraints.video);
        const wantsAudio = !!(constraints && constraints.audio);

        if (wantsVideo) {
          window.__vexa_gum_video_intercepted = (window.__vexa_gum_video_intercepted || 0) + 1;
          console.log('[Vexa] Intercepting video — returning canvas stream');

          // Get canvas video track from the GLOBAL (may have been refreshed by enableCamera)
          const currentStream = window.__vexa_canvas_stream || canvasStream;
          const canvasVideoTrack = currentStream.getVideoTracks()[0];

          if (wantsAudio) {
            // Need both video (from canvas) and audio (real mic)
            try {
              const audioStream = await origGetUserMedia({ audio: constraints.audio });
              const combinedStream = new MediaStream();
              combinedStream.addTrack(canvasVideoTrack.clone());
              for (const audioTrack of audioStream.getAudioTracks()) {
                combinedStream.addTrack(audioTrack);
              }
              console.log('[Vexa] Returning combined stream: canvas video + real audio');
              return combinedStream;
            } catch (audioErr) {
              // If audio fails, return just the canvas video
              console.warn('[Vexa] Audio getUserMedia failed, returning canvas video only:', audioErr);
              const videoOnlyStream = new MediaStream();
              videoOnlyStream.addTrack(canvasVideoTrack.clone());
              return videoOnlyStream;
            }
          } else {
            // Video only request — return canvas stream
            const videoOnlyStream = new MediaStream();
            videoOnlyStream.addTrack(canvasVideoTrack.clone());
            console.log('[Vexa] Returning canvas video only stream');
            return videoOnlyStream;
          }
        }

        // Audio-only or other requests — pass through to original
        return origGetUserMedia(constraints);
      };

      // ===== 3. Patch RTCPeerConnection =====
      // Track all connections AND intercept addTrack to swap video tracks.
      window.__vexa_peer_connections = [];
      window.__vexa_addtrack_intercepted = 0;
      window.__vexa_offer_video_forced = 0;
      const OrigRTC = window.RTCPeerConnection;

      // Patch addTrack on the prototype BEFORE creating any instances.
      // When Google Meet calls pc.addTrack(videoTrack, stream), we swap
      // the video track for our canvas track. This is the most reliable
      // interception point — it catches the track at the exact moment
      // it enters the WebRTC pipeline.
      const origAddTrack = OrigRTC.prototype.addTrack;
      OrigRTC.prototype.addTrack = function(track, ...streams) {
        // IMPORTANT: Read from window.__vexa_canvas_stream (the GLOBAL), not the
        // closure variable. enableCamera() may create a fresh captureStream(30)
        // with a new track ID, and we need to use whatever is current.
        const currentStream = window.__vexa_canvas_stream;
        if (track && track.kind === 'video' && currentStream) {
          const canvasTrack = currentStream.getVideoTracks()[0];
          if (canvasTrack) {
            window.__vexa_addtrack_intercepted = (window.__vexa_addtrack_intercepted || 0) + 1;
            console.log('[Vexa] addTrack intercepted: swapping video track for canvas track (original: ' + track.label + ')');
            return origAddTrack.call(this, canvasTrack, ...streams);
          }
        }
        return origAddTrack.call(this, track, ...streams);
      };

      // Also patch replaceTrack on RTCRtpSender to intercept any later
      // track swaps that Google Meet might do (e.g., camera toggle).
      // When Meet tries to set a video track, we substitute our canvas track.
      const origReplaceTrack = RTCRtpSender.prototype.replaceTrack;
      RTCRtpSender.prototype.replaceTrack = function(newTrack) {
        // IMPORTANT: Read from window.__vexa_canvas_stream (the GLOBAL), not the
        // closure variable. enableCamera() may create a fresh captureStream(30).
        const currentStream = window.__vexa_canvas_stream;
        if (newTrack && newTrack.kind === 'video' && currentStream) {
          const canvasTrack = currentStream.getVideoTracks()[0];
          // Only swap if the incoming track is NOT our canvas track
          if (canvasTrack && newTrack.id !== canvasTrack.id) {
            console.log('[Vexa] replaceTrack intercepted: substituting canvas track (blocked: ' + newTrack.label + ')');
            // CRITICAL: Don't just block — actually set our canvas track!
            // Returning Promise.resolve() would leave the sender with a null track.
            return origReplaceTrack.call(this, canvasTrack);
          }
        }
        return origReplaceTrack.call(this, newTrack);
      };

      // Ensure outbound video is present BEFORE offers are generated.
      // Teams guest/light meeting flow can create an offer with m=video inactive.
      // If no active video sender exists at offer-time, remote side never receives
      // a publishable camera track even if we replace tracks later.
      const origCreateOffer = OrigRTC.prototype.createOffer;
      OrigRTC.prototype.createOffer = async function(...offerArgs) {
        try {
          const currentStream = window.__vexa_canvas_stream;
          const canvasTrack = currentStream?.getVideoTracks?.()[0];
          if (canvasTrack) {
            const transceivers = this.getTransceivers ? this.getTransceivers() : [];
            let hasVideoSender = false;
            let attachedToExisting = false;

            for (const t of transceivers) {
              const receiverKind = t.receiver?.track?.kind;
              const senderKind = t.sender?.track?.kind;
              const isVideoTransceiver = receiverKind === 'video' || senderKind === 'video';
              if (!isVideoTransceiver) continue;

              // Force video-capable transceivers to allow sending.
              try {
                if (t.direction === 'inactive' || t.direction === 'recvonly') {
                  t.direction = 'sendrecv';
                }
              } catch {}

              if (t.sender?.track?.kind === 'video') {
                hasVideoSender = true;
                continue;
              }

              if (!t.sender?.track) {
                try {
                  await t.sender.replaceTrack(canvasTrack.clone());
                  attachedToExisting = true;
                  hasVideoSender = true;
                  console.log('[Vexa] createOffer pre-hook: attached canvas track to existing video transceiver (mid=' + (t.mid || 'null') + ')');
                } catch {}
              }
            }

            // If Teams did not keep a send-capable video transceiver, inject one.
            if (!hasVideoSender) {
              try {
                const tx = this.addTransceiver(canvasTrack.clone(), { direction: 'sendrecv' });
                window.__vexa_offer_video_forced = (window.__vexa_offer_video_forced || 0) + 1;
                console.log('[Vexa] createOffer pre-hook: added canvas video transceiver (mid=' + (tx?.mid || 'null') + ', attachedExisting=' + attachedToExisting + ')');
              } catch (addErr) {
                console.warn('[Vexa] createOffer pre-hook addTransceiver failed:', addErr);
              }
            }
          }
        } catch (offerHookErr) {
          console.warn('[Vexa] createOffer pre-hook failed:', offerHookErr);
        }
        return origCreateOffer.apply(this, offerArgs);
      };

      window.RTCPeerConnection = function(...args) {
        const pc = new OrigRTC(...args);
        window.__vexa_peer_connections.push(pc);
        console.log('[Vexa] New RTCPeerConnection created, total:', window.__vexa_peer_connections.length);
        pc.addEventListener('connectionstatechange', () => {
          if (pc.connectionState === 'closed' || pc.connectionState === 'failed') {
            const idx = window.__vexa_peer_connections.indexOf(pc);
            if (idx >= 0) window.__vexa_peer_connections.splice(idx, 1);
          }
        });
        return pc;
      };
      window.RTCPeerConnection.prototype = OrigRTC.prototype;
      // Copy static properties
      Object.keys(OrigRTC).forEach(key => {
        try { window.RTCPeerConnection[key] = OrigRTC[key]; } catch {}
      });

      // ===== 4. Patch enumerateDevices =====
      // Teams checks navigator.mediaDevices.enumerateDevices() to decide
      // whether to show the camera button. In a headless container there are
      // no physical cameras, so Teams disables the video toggle. We inject a
      // fake videoinput device so Teams enables the button. When Teams calls
      // getUserMedia, our patch above returns the canvas stream.
      const origEnumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices = async function() {
        const devices = await origEnumerateDevices();
        const hasVideo = devices.some(d => d.kind === 'videoinput');
        if (!hasVideo) {
          devices.push({
            deviceId: 'vexa-virtual-camera',
            kind: 'videoinput',
            label: 'Vexa Virtual Camera',
            groupId: 'vexa-virtual',
            toJSON() { return { deviceId: this.deviceId, kind: this.kind, label: this.label, groupId: this.groupId }; }
          });
          console.log('[Vexa] Injected virtual camera into enumerateDevices');
        }
        return devices;
      };

      console.log('[Vexa] getUserMedia + RTCPeerConnection + addTrack + createOffer + enumerateDevices patched for virtual camera');
      } catch (e) {
        console.error('[Vexa] Init script FAILED:', e);
      }
    })();
  `;
}
