/**
 * The bot's outgoing camera — SHARED browser module (bot + extension).
 *
 * The container has no camera and cannot screen-share under Xvfb, so what the meeting sees is a
 * CANVAS: the host draws on it, and the page publishes it as the bot's video track.
 *
 * A video track can enter a meeting by four routes, and all four are hooked at document-start,
 * before the page builds its peer connections:
 *   • `getUserMedia({video})`      → the canvas track, never a camera (audio passes through);
 *   • `RTCPeerConnection.addTrack` → a video track is swapped for the canvas track;
 *   • `RTCRtpSender.replaceTrack`  → the same, so a camera toggle cannot replace us with nothing;
 *   • `createOffer`                → a send-capable video transceiver carries the canvas BEFORE the
 *                                    offer is made, for platforms that would otherwise offer no video.
 * Every peer connection is registered so the host can push a fresh track after a renegotiation
 * (`applyVirtualCameraTrack`), and one `videoinput` device is advertised because some platforms hide
 * the camera button when the machine reports no camera at all.
 *
 * INCOMING media is deliberately untouched: the capture lane reads the page's own audio elements and
 * the gmeet lane reads participant tiles, so disabling or re-directing remote tracks here would take
 * the transcript down with it.
 */

export interface VirtualCameraOptions {
  log?: (m: string) => void;
  /** Canvas size. 720p by default: the meeting scales it anyway, and each pixel is CPU in a container. */
  width?: number;
  height?: number;
  /** Frames per second requested from the canvas. */
  fps?: number;
  /**
   * Make video send-capable before each offer (the `createOffer` route). True by default. A platform
   * whose server answers from its own model of the session (Zoom) rejects an offer that gained a
   * video section — "the order of m-lines in answer doesn't match" — and that connection, audio
   * included, never comes up; there the canvas rides only the video senders the platform creates.
   */
  shapeOffers?: boolean;
}

export interface VirtualCameraState {
  installed: boolean;
  width: number;
  height: number;
  videoTracks: number;
  peerConnections: number;
  pumping: boolean;
  intercepted: { getUserMedia: number; addTrack: number; replaceTrack: number; offers: number };
}

interface VirtualCameraGlobals {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  stream: MediaStream;
  pcs: RTCPeerConnection[];
  fps: number;
  pump: ReturnType<typeof setInterval> | null;
  shapeOffers: boolean;
  log: (m: string) => void;
  intercepted: { getUserMedia: number; addTrack: number; replaceTrack: number; offers: number };
}

const GLOBAL_KEY = "__vexaVirtualCamera";

function globals(): VirtualCameraGlobals | null {
  return (globalThis as any)[GLOBAL_KEY] ?? null;
}

/** The canvas track the meeting should be sending right now. */
function canvasTrack(vc: VirtualCameraGlobals): MediaStreamTrack | null {
  return vc.stream.getVideoTracks()[0] ?? null;
}

/**
 * Install the camera. Idempotent — returns false when it was already installed (every navigation
 * re-runs the init script, and the page must not end up with two canvases).
 */
export function installVirtualCamera(opts: VirtualCameraOptions = {}): boolean {
  if (globals()) return false;
  const log = opts.log || (() => {});
  const width = opts.width ?? 1280;
  const height = opts.height ?? 720;
  const fps = opts.fps ?? 15;

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d") as CanvasRenderingContext2D | null;
  if (!ctx) {
    log("no 2d context — the bot has no camera to show");
    return false;
  }
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, width, height);

  const vc: VirtualCameraGlobals = {
    canvas,
    ctx,
    stream: canvas.captureStream(fps),
    pcs: [],
    fps,
    pump: null,
    shapeOffers: opts.shapeOffers ?? true,
    log,
    intercepted: { getUserMedia: 0, addTrack: 0, replaceTrack: 0, offers: 0 },
  };
  (globalThis as any)[GLOBAL_KEY] = vc;

  hookGetUserMedia(vc);
  hookPeerConnection(vc);
  hookEnumerateDevices(vc);
  log(`installed (${width}x${height} @ ${fps}fps)`);
  return true;
}

function hookGetUserMedia(vc: VirtualCameraGlobals): void {
  const media = navigator.mediaDevices;
  const original = media.getUserMedia.bind(media);
  media.getUserMedia = async (constraints?: MediaStreamConstraints): Promise<MediaStream> => {
    if (!constraints || !constraints.video) return original(constraints);
    vc.intercepted.getUserMedia++;
    const track = canvasTrack(vc);
    if (!track) return original(constraints);
    const out = new MediaStream();
    out.addTrack(track.clone());
    if (constraints.audio) {
      // The microphone is real (the container's PulseAudio device) — only video is ours.
      try {
        const audio = await original({ audio: constraints.audio });
        for (const a of audio.getAudioTracks()) out.addTrack(a);
      } catch (e) {
        vc.log(`microphone unavailable, sending video only: ${String(e)}`);
      }
    }
    return out;
  };
}

function hookPeerConnection(vc: VirtualCameraGlobals): void {
  const Original = (globalThis as any).RTCPeerConnection;
  if (!Original) {
    vc.log("no RTCPeerConnection in this page — the camera cannot be published");
    return;
  }

  const originalAddTrack = Original.prototype.addTrack;
  Original.prototype.addTrack = function (track: MediaStreamTrack, ...streams: MediaStream[]) {
    const ours = canvasTrack(vc);
    if (ours && track && track.kind === "video" && track.id !== ours.id) {
      vc.intercepted.addTrack++;
      return originalAddTrack.call(this, ours, ...streams);
    }
    return originalAddTrack.call(this, track, ...streams);
  };

  const sender = (globalThis as any).RTCRtpSender;
  if (sender?.prototype?.replaceTrack) {
    const originalReplaceTrack = sender.prototype.replaceTrack;
    sender.prototype.replaceTrack = function (track: MediaStreamTrack | null) {
      const ours = canvasTrack(vc);
      // A platform swapping in its own camera track — or clearing the track on a camera toggle —
      // must not take the bot's video away; substitute ours instead of blocking, so the sender is
      // never left empty.
      if (ours && (!track || track.kind === "video") && track?.id !== ours.id) {
        vc.intercepted.replaceTrack++;
        return originalReplaceTrack.call(this, ours);
      }
      return originalReplaceTrack.call(this, track);
    };
  }

  const originalCreateOffer = Original.prototype.createOffer;
  Original.prototype.createOffer = async function (...args: unknown[]) {
    const ours = canvasTrack(vc);
    if (ours && vc.shapeOffers) {
      try {
        let carries = false;
        for (const t of this.getTransceivers?.() ?? []) {
          const isVideo = t.receiver?.track?.kind === "video" || t.sender?.track?.kind === "video";
          if (!isVideo) continue;
          if (t.direction === "inactive" || t.direction === "recvonly") t.direction = "sendrecv";
          if (t.sender?.track?.kind === "video") { carries = true; continue; }
          if (t.sender && !t.sender.track) { await t.sender.replaceTrack(ours); carries = true; }
        }
        if (!carries) {
          this.addTransceiver(ours, { direction: "sendrecv" });
          vc.intercepted.offers++;
        }
      } catch (e) {
        vc.log(`could not attach the camera before the offer: ${String(e)}`);
      }
    }
    return originalCreateOffer.apply(this, args as []);
  };

  (globalThis as any).RTCPeerConnection = function (this: unknown, ...args: unknown[]) {
    const pc = new Original(...args);
    vc.pcs.push(pc);
    pc.addEventListener("connectionstatechange", () => {
      if (pc.connectionState === "closed" || pc.connectionState === "failed") {
        const i = vc.pcs.indexOf(pc);
        if (i >= 0) vc.pcs.splice(i, 1);
      }
    });
    return pc;
  };
  (globalThis as any).RTCPeerConnection.prototype = Original.prototype;
  for (const key of Object.keys(Original)) {
    try { (globalThis as any).RTCPeerConnection[key] = Original[key]; } catch { /* read-only static */ }
  }
}

function hookEnumerateDevices(vc: VirtualCameraGlobals): void {
  const media = navigator.mediaDevices;
  if (!media.enumerateDevices) return;
  const original = media.enumerateDevices.bind(media);
  media.enumerateDevices = async (): Promise<MediaDeviceInfo[]> => {
    const devices = await original();
    if (devices.some((d) => d.kind === "videoinput")) return devices;
    // Some platforms hide the camera button when the machine reports no camera; the request that
    // button makes is answered by the getUserMedia hook above.
    return [...devices, {
      deviceId: "vexa-virtual-camera",
      kind: "videoinput",
      label: "Vexa Virtual Camera",
      groupId: "vexa-virtual-camera",
      toJSON() { return { deviceId: this.deviceId, kind: this.kind, label: this.label, groupId: this.groupId }; },
    } as MediaDeviceInfo];
  };
}

/** Draw an image (URL or data URI) centred on black, `contain`-fit. False when it cannot be loaded. */
export function drawVirtualCameraImage(src: string): Promise<boolean> {
  const vc = globals();
  if (!vc) return Promise.resolve(false);
  return new Promise<boolean>((resolve) => {
    const img = new Image();
    img.onload = () => {
      const { canvas, ctx } = vc;
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.imageSmoothingEnabled = true;
      const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
      const w = img.width * scale;
      const h = img.height * scale;
      ctx.drawImage(img, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
      resolve(true);
    };
    img.onerror = () => { vc.log("the image could not be loaded"); resolve(false); };
    img.src = src;
  });
}

/** Black — what the bot shows when it has nothing to show. */
export function clearVirtualCamera(): boolean {
  const vc = globals();
  if (!vc) return false;
  vc.ctx.fillStyle = "#000000";
  vc.ctx.fillRect(0, 0, vc.canvas.width, vc.canvas.height);
  return true;
}

/**
 * Keep frames flowing. `captureStream` emits a frame only when the canvas changes, so a still avatar
 * would reach the meeting as one or two frames and then freeze; the pump touches a single pixel per
 * tick, which is invisible and enough to keep the track live.
 */
export function startVirtualCameraPump(): boolean {
  const vc = globals();
  if (!vc || vc.pump) return false;
  let flip = false;
  vc.pump = setInterval(() => {
    try {
      const pixel = vc.ctx.getImageData(0, 0, 1, 1);
      pixel.data[3] = flip ? 254 : 255;   // alpha, one step — invisible at these values
      flip = !flip;
      vc.ctx.putImageData(pixel, 0, 0);
    } catch { /* a page teardown mid-tick must not throw into the timer */ }
  }, Math.max(1, Math.round(1000 / vc.fps)));
  return true;
}

export function stopVirtualCameraPump(): boolean {
  const vc = globals();
  if (!vc?.pump) return false;
  clearInterval(vc.pump);
  vc.pump = null;
  return true;
}

/**
 * Push a FRESH canvas track onto every live video sender. Called after admission and whenever the
 * platform renegotiates: a camera toggle can end the track a sender holds, and a track that has
 * ended shows the other participants nothing.
 */
export async function applyVirtualCameraTrack(): Promise<{ replaced: number; senders: number; errors: string[] }> {
  const vc = globals();
  if (!vc) return { replaced: 0, senders: 0, errors: ["not installed"] };
  vc.stream = vc.canvas.captureStream(vc.fps);
  const track = canvasTrack(vc);
  if (!track) return { replaced: 0, senders: 0, errors: ["no canvas track"] };

  // ONLY the video senders. A sender holding no track is not necessarily a video sender — an audio
  // transceiver whose track has not been attached yet looks identical — and pushing video at it
  // throws "Track kind does not match Sender kind". The transceiver says which kind a sender carries;
  // a sender with no track and no incoming track is left to the createOffer hook, which adds a
  // transceiver of its own.
  const videoSenders = new Set<RTCRtpSender>();
  for (const pc of vc.pcs) {
    if (pc.connectionState === "closed") continue;
    for (const t of pc.getTransceivers?.() ?? []) {
      const kind = t.receiver?.track?.kind ?? t.sender?.track?.kind;
      if (kind === "video" && t.sender) videoSenders.add(t.sender);
    }
    for (const sender of pc.getSenders?.() ?? []) {
      if (sender.track?.kind === "video") videoSenders.add(sender);
    }
  }

  const errors: string[] = [];
  let replaced = 0;
  const senders = videoSenders.size;
  for (const sender of videoSenders) {
    try {
      await sender.replaceTrack(track);
      replaced++;
    } catch (e) {
      errors.push(String(e));
    }
  }
  vc.log(`camera track applied to ${replaced}/${senders} sender(s)`);
  return { replaced, senders, errors };
}

/**
 * How many video frames the meeting has actually sent from this camera. The canvas can be perfect and
 * the track attached while the connection sends nothing; this reads the peer connections' own stats,
 * which is the only place that difference shows.
 */
export async function virtualCameraFramesSent(): Promise<number> {
  const vc = globals();
  if (!vc) return 0;
  let frames = 0;
  for (const pc of vc.pcs) {
    if (pc.connectionState === "closed") continue;
    try {
      const stats = await pc.getStats();
      stats.forEach((report: any) => {
        if (report.type === "outbound-rtp" && report.kind === "video") frames += report.framesSent || 0;
      });
    } catch { /* a connection torn down mid-read is not a diagnostic failure */ }
  }
  return frames;
}

export function virtualCameraState(): VirtualCameraState {
  const vc = globals();
  if (!vc) {
    return { installed: false, width: 0, height: 0, videoTracks: 0, peerConnections: 0, pumping: false,
             intercepted: { getUserMedia: 0, addTrack: 0, replaceTrack: 0, offers: 0 } };
  }
  return {
    installed: true,
    width: vc.canvas.width,
    height: vc.canvas.height,
    videoTracks: vc.stream.getVideoTracks().length,
    peerConnections: vc.pcs.length,
    pumping: vc.pump !== null,
    intercepted: { ...vc.intercepted },
  };
}
