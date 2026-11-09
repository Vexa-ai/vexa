// Runs in his meeting tab (Zoom's web client, Google Meet, Microsoft Teams), in the extension's own (isolated) world. It is the only
// way frames leave the tab: while a capture he started from the toolbar runs, it forwards page-main.js's per-speaker frames over one extension
// port to the worker, which hands them to the panel tab that owns the voice note. No capture asked for, no port:
// whatever the page posts is dropped here.
(() => {
  if (window.__vexaZoomRelay) return;
  window.__vexaZoomRelay = true;

  const TAG = "vexa-capture.v1";
  const toMain = (m) => window.postMessage({ [TAG]: true, from: "relay", ...m }, "*");   // its own window only (an about:srcdoc frame has no usable origin string); e.source is checked
  const waits = new Map();
  let seq = 0, port = null, beat = null;

  function ask(type, extra, ms) {
    return new Promise((done) => {
      const id = ++seq;
      const t = setTimeout(() => { waits.delete(id); done(null); }, ms || 1500);
      waits.set(id, (m) => { clearTimeout(t); waits.delete(id); done(m); });
      toMain({ type, id, ...(extra || {}) });
    });
  }

  function b64(buf) {
    const u = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
    return btoa(s);
  }

  window.addEventListener("message", (e) => {
    const m = e.data;
    if (e.source !== window || !m || m[TAG] !== true || m.from !== "main") return;
    if (m.id && waits.has(m.id)) { waits.get(m.id)(m); return; }
    if (!port) return;
    try {
      if (m.type === "frame" && m.pcm instanceof ArrayBuffer && m.pcm.byteLength <= 65536 && Number.isInteger(m.track)) {
        port.postMessage({ t: "f", track: m.track, ts: Number(m.ts), name: String(m.name || "").slice(0, 200), pcm: b64(m.pcm) });
      } else if (m.type === "observe" && m.data && typeof m.data === "object") {
        port.postMessage({ t: "o", data: { type: String(m.data.type || ""), state: m.data.state, channel: m.data.channel, streams: m.data.streams } });
      }
    } catch { /* the port closed under us: onDisconnect cleans up */ }
  });

  function close() {
    clearInterval(beat); beat = null;
    try { if (port) port.disconnect(); } catch { /* gone */ }
    port = null;
    toMain({ type: "stop" });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (!msg || typeof msg.type !== "string") return;
    if (msg.type === "vexa-pertrack-probe") {
      ask("probe").then((m) => reply(m ? { ready: m.ready === true, platform: String(m.platform || ""), needsTracks: m.needsTracks === true, late: m.late === true, streams: m.streams | 0, hooked: m.hooked | 0, pcs: m.pcs | 0, frames: m.frames | 0 } : { ready: false, platform: "", needsTracks: false, streams: 0, hooked: 0, pcs: 0, frames: 0, absent: true }));
      return true;
    }
    if (msg.type === "vexa-pertrack-start") {
      if (port) close();
      port = chrome.runtime.connect({ name: "vexa-pertrack" });
      port.onDisconnect.addListener(() => { if (port) { port = null; clearInterval(beat); beat = null; toMain({ type: "stop" }); } });
      // A beat every 10 s: silence sends no frames, and the worker must not idle out under a quiet meeting.
      beat = setInterval(() => { try { if (port) port.postMessage({ t: "hb" }); } catch { /* closed */ } }, 10000);
      ask("start", { selfName: String(msg.selfName || "").slice(0, 120) }).then((m) => {
        if (!m) { close(); reply({ ok: false, error: "The meeting page did not answer." }); return; }
        reply({ ok: true, channels: m.channels | 0, context: String(m.context || "") });
      });
      return true;
    }
    if (msg.type === "vexa-pertrack-stop") { close(); reply({ ok: true }); }
  });
  // Leaving the page ends the per-track side; the worker re-asks the new page if the capture is still on.
  window.addEventListener("pagehide", () => { if (port) close(); });
})();
