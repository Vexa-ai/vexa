# Native SDK addon source

Vexa-owned N-API wrapper recovered from the historical SDK repair branch. The joining controller invokes initialization, authentication, join, leave and cleanup only. Legacy raw-media methods remain dormant; capture integration is outside this change.

## Build

Copy `native/` to a private Linux x86_64 build directory. Install node-addon-api and node-gyp there; install Qt5Core development tooling. Download the Linux Meeting SDK separately from its provider. Run `npx node-gyp rebuild --zoom_sdk_dir="$ZOOM_SDK_DIR"`. Ensure the supplied runtime's `libmeetingsdk.so.1` loader name resolves. Set `ZOOM_SDK_ADDON` to the resulting `build/Release/zoom_sdk_wrapper.node`.

The controller sets the SDK Qt library path before the SDK root. Dependencies and native artifacts belong outside this source tree. No automatic download, published image integration, vendor headers, libraries or archives are included. Optional ZAK maps to `JoinParam4WithoutLogin.userZAK`; OBF maps to `onBehalfToken`. Both remain backed by wrapper-owned string storage during native calls. Joining disables video and audio. The process boundary contains crashes and permits bounded forced cleanup.

## Verify

Compile against the operator's chosen SDK; the JavaScript fixture tests cannot establish native ABI compatibility. This service is an experimental runtime artifact, not a network service or production deployment.
