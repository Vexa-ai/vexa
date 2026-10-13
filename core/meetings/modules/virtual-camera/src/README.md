# virtual-camera/src

Front door [`index.ts`](index.ts). One browser piece: [`virtual-camera.ts`](virtual-camera.ts) —
`installVirtualCamera` (the canvas + the `getUserMedia` / `addTrack` / `replaceTrack` / `createOffer`
hooks that make the meeting send it), `drawVirtualCameraImage` / `clearVirtualCamera` (what it shows),
`startVirtualCameraPump` (keep frames flowing for a still image), `applyVirtualCameraTrack` (push a
fresh track after a renegotiation) and `virtualCameraState` (diagnostics).

`virtual-camera.test.ts` drives all of it against an in-memory DOM/WebRTC shim — no browser, no
Playwright. The page behaviour itself is validated in a live meeting.
