// Runs in his meeting tab (Zoom's web client, Google Meet, Microsoft Teams), in the PAGE's world, at document_start.
// It runs the bot's own capture for that platform (vendor/vexa-page-capture.js: each platform's PageCapture,
// built from the bot's modules): one channel per participant, named from the platform's active-speaker tile.
// - Zoom and Teams ride WebRTC: the hook is installed now, before the page builds its peer connections, and only
//   holds each remote track's handle; its mirror elements are muted (playback:false): the page already plays it.
//   Meet exposes its participants as media elements: no hook.
// - Until a capture is asked for, no audio is read and nothing leaves the tab.
// - Frames go only to page-relay.js (same tab), which forwards them only while a capture he started is running.
(() => {
  const P = globalThis.VexaPageCapture;
  if (!P || window.__vexaPageMain) return;
  // the meeting may run in an iframe (Zoom's web client does); an about:blank/srcdoc frame takes its parent's host
  let host = location.hostname;
  if (!host) { try { host = window.parent.location.hostname; } catch { host = ""; } }
  const platform = host === "meet.google.com" ? "google_meet"
    : (host === "zoom.us" || host.endsWith(".zoom.us")) ? "zoom"
    : (host === "teams.microsoft.com" || host === "teams.live.com" || host === "teams.cloud.microsoft") ? "teams" : "";
  const impl = platform && P.platforms[platform];
  if (!impl) return;
  window.__vexaPageMain = true;
  const late = document.readyState !== "loading";   // in after the page had loaded: it may have connected already
  if (impl.hook) P.installRemoteAudioHook({ playback: false });
  const ready = () => !impl.hook || window.__vexaRemoteAudioHookInstalled === true;

  const TAG = "vexa-capture.v1";
  const post = (m) => window.postMessage({ [TAG]: true, from: "main", ...m }, "*");   // its own window only (an about:srcdoc frame has no usable origin string); e.source is checked
  const streams = () => window.__vexaCapturedRemoteAudioStreams || [];
  let cap = null;

  window.addEventListener("message", (e) => {
    const m = e.data;
    if (e.source !== window || !m || m[TAG] !== true || m.from !== "relay") return;
    if (m.type === "probe") {
      const live = streams().filter((s) => { try { return s.getAudioTracks().some((t) => t.readyState === "live"); } catch { return false; } }).length;
      const pcs = (window.__vexa_peer_connections || []).length;
      const frames = document.querySelectorAll("iframe").length;
      post({ type: "probe", id: m.id, platform, ready: ready(), needsTracks: impl.hook === true, late, streams: live,
        hooked: streams().length, pcs, frames });
    } else if (m.type === "start") {
      if (!cap) {
        cap = impl.create({
          streams,                         // zoom/teams: the hooked tracks (Meet finds its own media elements)
          selfName: typeof m.selfName === "string" && m.selfName ? m.selfName : undefined,
          // Participants are tracks 1.. of the note: track 0 is his own microphone (VOICE_NOTE_OWNER_TRACK).
          onAudio: (ch, name, pcm, ts) => post({ type: "frame", track: ch + 1, name: name || "", ts, pcm: pcm.buffer }),
          observe: (_kind, data) => {
            const t = data && data.type;
            if (t === "pertrack-context" || t === "pertrack-capture-failed" || t === "pertrack-bind" || t === "pertrack-flip"
              || t === "pertrack-topology" || t === "pagecapture-started" || t === "pagecapture-failed"
              || t === "mix-topology" || t === "main-audio-absent") post({ type: "observe", data });
          },
        });
      }
      post({ type: "started", id: m.id, channels: cap.channels(), context: cap.contextState() });
    } else if (m.type === "stop") {
      try { if (cap) cap.stop(); } catch { /* already gone */ }
      cap = null;
    }
  });
})();
