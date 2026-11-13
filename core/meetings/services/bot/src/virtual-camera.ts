/**
 * Virtual camera (AIM-2050) — the bot's camera tile shows invocation.v1 `defaultAvatarUrl`.
 *
 * Ported from the 0.11 bot (services/vexa-bot/core/src/services/screen-content.ts,
 * getVirtualCameraInitScript), narrowed to what a static avatar needs:
 *   • a hidden canvas with the avatar drawn on it, kept emitting frames;
 *   • getUserMedia({video}) answers with the canvas track, enumerateDevices lists a camera;
 *   • addTrack / replaceTrack swap any outgoing video track for the canvas track;
 *   • Teams only: createOffer makes sure a send-capable video transceiver carries the canvas
 *     (Teams light meetings otherwise offer m=video inactive and never publish the camera).
 * The RTCPeerConnection constructor is deliberately NOT replaced: the 0.12 Meet roster/audio
 * hook (installRemoteAudioHook) owns that; prototype patches coexist with it.
 *
 * The image is fetched Node-side and inlined as a data: URI, so the page never does a
 * cross-origin load (a CORS-less logo URL would taint the canvas / fail with crossOrigin set).
 */

/** Platforms whose join flow keeps the camera on for us (@vexa/join keepCameraOn). Not Jitsi, and not
 *  Teams: publishing the canvas there makes Teams build an offer Chromium rejects (BUNDLE header-
 *  extension id collision) and the call drops ~40 s after admission (AIM-2065, 2026-09-22). */
const VIRTUAL_CAMERA_PLATFORMS = new Set(['google_meet', 'zoom']);

export function wantsVirtualCamera(inv: { platform: string; defaultAvatarUrl?: string }): boolean {
  return VIRTUAL_CAMERA_PLATFORMS.has(inv.platform) && !!inv.defaultAvatarUrl;
}

const MAX_AVATAR_BYTES = 1_000_000;
const FETCH_TIMEOUT_MS = 5_000;

/** Fetch `url` and return it as a data: URI, or null when it is unreachable / not an image. */
export async function resolveAvatarDataUri(
  url: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  if (url.startsWith('data:image/')) return url;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetchImpl(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const type = (res.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
    if (!type.startsWith('image/')) throw new Error(`not an image (${type || 'no content-type'})`);
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length > MAX_AVATAR_BYTES) throw new Error(`too large (${buf.length} bytes)`);
    return `data:${type};base64,${buf.toString('base64')}`;
  } catch (e) {
    console.error(`[bot] avatar ${url} unusable, camera tile stays blank: ${String(e)}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Page init script installing the virtual camera. `avatar` null → a blank tile (never Chrome's
 *  fake test pattern). Runs at document-start on every navigation; top frame only, once. */
export function buildVirtualCameraInitScript(avatar: string | null): string {
  return `(() => {
  if (window.top !== window || window.__vexa_vcam) return;
  window.__vexa_vcam = true;
  try {
    var W = 1280, H = 720;
    var canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    canvas.style.cssText = 'position:fixed;top:-9999px;left:-9999px;';
    var ctx = canvas.getContext('2d');
    var paint = function (img) {
      ctx.fillStyle = '#C9D4E6'; // Light steel blue: Meet flags a dark tile as "Camera might be blocked" and hides it (AIM-2804); white glared (AIM-2330)
      ctx.fillRect(0, 0, W, H);
      if (!img) return;
      var iw = img.naturalWidth || 512, ih = img.naturalHeight || 512;
      var box = H * 0.4, s = Math.min(box / iw, box / ih);
      ctx.drawImage(img, (W - iw * s) / 2, (H - ih * s) / 2, iw * s, ih * s);
    };
    paint(null);
    var src = ${JSON.stringify(avatar)};
    if (src) { var img = new Image(); img.onload = function () { paint(img); }; img.src = src; }
    var stream = canvas.captureStream(15);
    var track = function () { return stream.getVideoTracks()[0]; };
    // Static content emits only a frame or two; Meet then drops the tile. Nudge one pixel.
    var flip = false;
    setInterval(function () {
      try { var p = ctx.getImageData(0, 0, 1, 1); p.data[3] = flip ? 254 : 255; flip = !flip; ctx.putImageData(p, 0, 0); } catch (e) {}
    }, 200);
    var attach = function () { document.body ? document.body.appendChild(canvas) : document.addEventListener('DOMContentLoaded', attach); };
    attach();

    var md = navigator.mediaDevices;
    var gum = md.getUserMedia.bind(md);
    md.getUserMedia = async function (c) {
      if (!c || !c.video) return gum(c);
      var out = new MediaStream([track().clone()]);
      if (c.audio) {
        try { (await gum({ audio: c.audio })).getAudioTracks().forEach(function (t) { out.addTrack(t); }); } catch (e) {}
      }
      return out;
    };
    var enumerate = md.enumerateDevices.bind(md);
    md.enumerateDevices = async function () {
      var list = await enumerate();
      if (!list.some(function (d) { return d.kind === 'videoinput'; })) {
        list.push({ deviceId: 'aimable-virtual-camera', kind: 'videoinput', label: 'Virtual Camera', groupId: 'aimable-virtual',
          toJSON: function () { return { deviceId: this.deviceId, kind: this.kind, label: this.label, groupId: this.groupId }; } });
      }
      return list;
    };
    var addTrack = RTCPeerConnection.prototype.addTrack;
    RTCPeerConnection.prototype.addTrack = function (t) {
      var rest = Array.prototype.slice.call(arguments, 1);
      return addTrack.apply(this, [t && t.kind === 'video' ? track() : t].concat(rest));
    };
    var replaceTrack = RTCRtpSender.prototype.replaceTrack;
    RTCRtpSender.prototype.replaceTrack = function (t) {
      return replaceTrack.call(this, t && t.kind === 'video' && t.id !== track().id ? track() : t);
    };
    (window.logBot || console.log)('[vcam] virtual camera stream installed (avatar: ' + (src ? 'yes' : 'blank') + ')');
  } catch (e) {
    (window.logBot || console.error)('[vcam] install failed: ' + e);
  }
})();`;
}
