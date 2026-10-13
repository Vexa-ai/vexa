/**
 * @vexa/virtual-camera — what the bot shows in the meeting (browser).
 *
 * A canvas the host draws on, published as the bot's outgoing video track: the container has no
 * camera, and a screen share cannot start under Xvfb. Runs INSIDE the meeting page (injected by the
 * bot at document-start, or loaded by the extension) — zero node/back imports; the host draws through
 * these functions and pushes the track when the platform renegotiates.
 */
export {
  installVirtualCamera,
  drawVirtualCameraImage,
  clearVirtualCamera,
  startVirtualCameraPump,
  stopVirtualCameraPump,
  applyVirtualCameraTrack,
  virtualCameraState,
  virtualCameraFramesSent,
} from "./virtual-camera.js";
export type { VirtualCameraOptions, VirtualCameraState } from "./virtual-camera.js";
