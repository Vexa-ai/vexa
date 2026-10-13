# @vexa/virtual-camera — what the bot shows in the meeting (browser)

_meetings/ · module · a canvas the host draws on → the bot's outgoing video track._

Runs **inside the meeting page**, installed at document-start before the page builds its
`RTCPeerConnection`s. The container has no camera, and a screen share cannot be started under Xvfb, so
the bot's video is a **canvas**: the host draws an avatar or an image on it, and the page publishes it
as the bot's camera.

- `installVirtualCamera` — creates the canvas + `captureStream()` and hooks the four places a video
  track can enter the meeting: `getUserMedia` (returns the canvas instead of a camera),
  `RTCPeerConnection.addTrack` and `RTCRtpSender.replaceTrack` (substitute the canvas track), and
  `createOffer` (make sure a send-capable video transceiver carries it before the offer is made). It
  also registers every peer connection and advertises one `videoinput` device, because some platforms
  hide the camera button when the machine reports no camera at all. Incoming media is left untouched —
  the capture lane reads the page's own audio elements.
- `drawVirtualCameraImage` / `clearVirtualCamera` — what the canvas shows (`contain`-fit on black).
- `startVirtualCameraPump` — `captureStream` emits a frame only when the canvas changes, so a still
  image would freeze after one or two frames. The pump touches one pixel per tick to keep frames flowing.
- `applyVirtualCameraTrack` — push a fresh canvas track onto every live sender, for the moment a
  platform renegotiates or the camera toggle kills the old track.
- `virtualCameraState` — what is installed and what the hooks have intercepted, for diagnostics.

`virtual-camera.test.ts` drives all of it against an in-memory DOM/WebRTC shim (no browser); the page
behaviour itself is validated in a live meeting.
