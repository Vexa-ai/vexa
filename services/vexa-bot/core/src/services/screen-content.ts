import { Page } from 'playwright-core';
import { log } from '../utils';
import * as fs from 'fs';
import * as path from 'path';

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

  constructor(page: Page) {
    this.page = page;
    // Load the default Vexa logo from assets
    this._loadDefaultAvatar();
  }

  private _loadDefaultAvatar(): void {
    try {
      // Try multiple paths (dev vs Docker)
      const possiblePaths = [
        path.join(__dirname, '../../assets/vexa-logo-default.png'),
        path.join(__dirname, '../assets/vexa-logo-default.png'),
        '/app/assets/vexa-logo-default.png',
      ];
      for (const p of possiblePaths) {
        if (fs.existsSync(p)) {
          const buf = fs.readFileSync(p);
          this._defaultAvatarDataUri = `data:image/png;base64,${buf.toString('base64')}`;
          log(`[ScreenContent] Default avatar loaded from ${p} (${buf.length} bytes)`);
          return;
        }
      }
      log('[ScreenContent] Default avatar file not found, using fallback');
    } catch (err: any) {
      log(`[ScreenContent] Failed to load default avatar: ${err.message}`);
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
  }

  /**
   * Turn on the camera button in Google Meet if it's off.
   * The getUserMedia patch ensures that when Meet gets the camera stream,
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

    // Click "Turn on camera" if it's visible (means camera is currently off)
    // Try multiple selector patterns for i18n support and Google Meet UI variations
    const turnOnCameraBtn = this.page.locator([
      'button[aria-label*="Turn on camera"]',
      'button[aria-label*="turn on camera"]',
      'button[aria-label*="Включить камеру"]',
      'button[aria-label*="camera" i][aria-label*="on" i]',
      'button[data-tooltip*="Turn on camera"]',
      'button[data-tooltip*="camera" i]',
    ].join(', ')).first();

    try {
      await turnOnCameraBtn.waitFor({ state: 'visible', timeout: 5000 });
      const label = await turnOnCameraBtn.getAttribute('aria-label');
      log(`[ScreenContent] Found camera button: "${label}", clicking...`);
      await turnOnCameraBtn.click({ force: true });
      log('[ScreenContent] Clicked camera button — getUserMedia patch will provide canvas stream');
      // Wait for camera to initialize and getUserMedia to be called
      await this.page.waitForTimeout(3000);
    } catch {
      log('[ScreenContent] Camera button not found — trying "Turn off camera" check (maybe already on)');
      // Check if camera is already on
      const turnOffCameraBtn = this.page.locator([
        'button[aria-label*="Turn off camera"]',
        'button[aria-label*="turn off camera"]',
        'button[aria-label*="Выключить камеру"]',
      ].join(', ')).first();
      try {
        await turnOffCameraBtn.waitFor({ state: 'visible', timeout: 2000 });
        log('[ScreenContent] Camera is already ON (found "Turn off camera" button)');
      } catch {
        log('[ScreenContent] Neither camera on nor off button found — camera may be unavailable');
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
   * Draw an avatar image (small, top-left corner) on a black canvas background.
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
          // Clear to black
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, canvas.width, canvas.height);

          // Draw the avatar in top-left corner (max ~15% of canvas height)
          const maxSize = Math.max(Math.round(canvas.height * 0.15), 120);
          const scale = Math.min(maxSize / img.width, maxSize / img.height);
          const w = img.width * scale;
          const h = img.height * scale;
          const padding = 30;
          const x = padding;
          const y = padding;

          ctx.drawImage(img, x, y, w, h);
          resolve();
        };
        img.onerror = () => {
          // Fallback: black screen
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
        // Draw an initial "Vexa" branded screen so we know it's working
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, 1920, 1080);
        ctx.fillStyle = '#7c3aed';
        ctx.font = 'bold 72px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('V', 960, 500);
        ctx.fillStyle = '#a78bfa';
        ctx.font = '28px sans-serif';
        ctx.fillText('Vexa Bot', 960, 580);
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

          // Get canvas video track
          const canvasVideoTrack = canvasStream.getVideoTracks()[0];

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
      const OrigRTC = window.RTCPeerConnection;

      // Patch addTrack on the prototype BEFORE creating any instances.
      // When Google Meet calls pc.addTrack(videoTrack, stream), we swap
      // the video track for our canvas track. This is the most reliable
      // interception point — it catches the track at the exact moment
      // it enters the WebRTC pipeline.
      const origAddTrack = OrigRTC.prototype.addTrack;
      OrigRTC.prototype.addTrack = function(track, ...streams) {
        if (track && track.kind === 'video' && canvasStream) {
          const canvasTrack = canvasStream.getVideoTracks()[0];
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
      const origReplaceTrack = RTCRtpSender.prototype.replaceTrack;
      RTCRtpSender.prototype.replaceTrack = function(newTrack) {
        if (newTrack && newTrack.kind === 'video' && canvasStream) {
          const canvasTrack = canvasStream.getVideoTracks()[0];
          // Only swap if the incoming track is NOT our canvas track
          if (canvasTrack && newTrack.id !== canvasTrack.id) {
            console.log('[Vexa] replaceTrack intercepted: keeping canvas track (blocked: ' + newTrack.label + ')');
            // Return resolved promise — we keep our canvas track
            return Promise.resolve();
          }
        }
        return origReplaceTrack.call(this, newTrack);
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

      console.log('[Vexa] getUserMedia + RTCPeerConnection + addTrack patched for virtual camera');
    })();
  `;
}
