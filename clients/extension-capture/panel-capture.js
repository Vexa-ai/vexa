// Runs in an open Vexa panel tab, as the panel's own origin: the voice note is made and streamed exactly as the
// panel's own voice notes are (POST /api/bots {platform: voice_note}, then WS /voice-notes/{id}/stream with his
// session cookie). The only difference is the audio: another tab's, handed over by Chrome (tabCapture), instead
// of the microphone. No key is read, stored or sent by this script.
// Two modes (background.js picks one per press):
//   one stream:  the tab's audio + his mic, summed on track 0 (0.2.3);
//   per speaker: his mic alone on track 0 (the note's owner), and each Zoom participant on its own track 1..,
//                named, from the bot's own Zoom capture running in the Zoom tab (page-main.js), relayed here over
//                an extension port and sent as capture.v1 named frames (vendor/vexa-capture-codec.js, the codec
//                the door decodes with). If per speaker carries nothing while the tab plays sound for 20 s, it
//                falls back to one stream by itself.
(() => {
  if (window.__vexaCapture) return;
  window.__vexaCapture = true;

  const RATE = 16000;
  const FRAME = RATE / 2;                 // half a second per capture.v1 frame (the door takes at most one)
  const sessions = new Map();             // meeting tab id -> session
  const MAX_TRACK = 63;                   // the note's lane takes tracks 0..63 (VOICE_NOTE_MAX_TRACKS)
  const MAX_PT_SAMPLES = 16000;           // a per-track frame is 4096 samples; anything past a second is refused
  const FALLBACK_MS = 20000;              // the tab plays sound, per speaker carries nothing: one stream instead
  const FALLBACK_LOUD_SECONDS = 5;        // …for at least this many loud seconds in that window (a chime is not a meeting)
  const PORT_GONE_MS = 5000;              // the per-speaker port dropped and no new one came: one stream instead
  const RELOAD_GRACE_MS = 30000;          // …unless the meeting tab is reloading: its new page is re-asked when loaded
  const MAX_TS_SKEW_MS = 10000;           // a per-speaker frame stamped further than this from now is refused
  const MAX_BUFFERED = 8 * 1024 * 1024;   // the note's socket is stalled: end the capture rather than grow
  const Codec = globalThis.VexaCaptureCodec;
  function encode(tsMs, pcm) {            // capture.v1 no-name audio frame: Int32 track, Float64 ms, Float32 PCM
    const buf = new ArrayBuffer(12 + pcm.length * 4);
    const v = new DataView(buf);
    v.setInt32(0, 0, true);
    v.setFloat64(4, tsMs, true);
    for (let i = 0; i < pcm.length; i++) v.setFloat32(12 + i * 4, pcm[i], true);
    return buf;
  }

  function resampler(from) {              // linear, carrying the fraction across chunks
    const step = from / RATE;
    let pos = 1, last = 0;
    return (x) => {
      const ext = new Float32Array(x.length + 1);
      ext[0] = last; ext.set(x, 1);
      const out = [];
      while (pos + 1 < ext.length) {
        const i = Math.floor(pos), f = pos - i;
        out.push(ext[i] * (1 - f) + ext[i + 1] * f);
        pos += step;
      }
      pos -= x.length;
      last = x[x.length - 1] || 0;
      return Float32Array.from(out);
    };
  }

  // --- the one visible sign on the panel side: a pill while anything is captured ----------------------------
  const pill = document.createElement("div");
  pill.setAttribute("data-vexa-capture", "");
  pill.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;display:none;gap:8px;align-items:center;"
    + "padding:6px 12px;border-radius:999px;background:#d93025;color:#fff;font:600 12px/1.4 system-ui,sans-serif;"
    + "box-shadow:0 2px 8px rgba(0,0,0,.25)";
  document.documentElement.appendChild(pill);
  function render() {
    const live = [...sessions.values()];
    if (!live.length) { pill.style.display = "none"; return; }
    pill.style.display = "flex";
    pill.textContent = "";
    for (const s of live) {
      const a = document.createElement("a");
      a.href = `/panel/meeting/voice_note/${encodeURIComponent(s.native || "")}`;
      a.style.cssText = "color:#fff;text-decoration:none";
      const mins = Math.floor(s.receivedSec / 60), secs = Math.floor(s.receivedSec % 60);
      a.textContent = `● Capturing ${s.label || "a tab"}${s.mode === "pertrack" ? " · per speaker" : ""}${s.micOff ? " (not your mic)" : " + your mic"} · ${mins}:${String(secs).padStart(2, "0")}${s.part > 1 ? ` · part ${s.part}` : ""}`;
      pill.appendChild(a);
      const p = s.probe;
      const facts = document.createElement("span");
      facts.style.cssText = "font-weight:400;opacity:.9";
      const page = !p || p.absent ? "page capture absent"
        : `${p.platform || "?"}: hook ${p.ready ? "✓" : "✗"} · ${p.pcs | 0} conn · ${p.hooked | 0} tracks (${p.streams | 0} live)`;
      facts.textContent = ` · ${page} · ${s.ptFrames} speaker frames${s.fellBack ? ` · fell back: ${s.fellBack}` : ""}`;
      pill.appendChild(facts);
    }
  }

  // His name as the panel knows him (the signed-in profile): the per-speaker capture never gives it to a remote
  // channel (the resolver's self-exclusion). Missing → no guard, as before.
  async function selfName() {
    try {
      const r = await within(1500, fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" }), "profile timed out");
      const d = r.ok ? await r.json() : null;
      const n = d && d.user && typeof d.user.name === "string" ? d.user.name.trim() : "";
      return n.slice(0, 120);
    } catch { return ""; }
  }

  async function openNote(s) {
    const r = await fetch("/api/bots", {
      method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
      body: JSON.stringify({ platform: "voice_note" }),
    });
    if (!r.ok) {
      let detail = `Vexa refused the voice note (${r.status}).`;
      try { const b = await r.json(); if (typeof b?.detail === "string") detail = b.detail; } catch { /* keep the status */ }
      throw new Error(detail);
    }
    const m = await r.json();
    s.native = m.native_meeting_id;
    s.part += 1;
    s.trackMap = new Map(); s.nextTrack = 1;   // a new note part numbers its speakers afresh
    s.ready = false;
    s.ws = new WebSocket(`wss://${location.host}/voice-notes/${encodeURIComponent(s.native)}/stream`);
    s.ws.binaryType = "arraybuffer";
    s.ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "ready") { s.ready = true; s.t0 = Date.now(); s.sent = 0; }
      else if (msg.type === "progress") { s.receivedSec = s.base + (msg.received_sec || 0); render(); }
      else if (msg.type === "error" && msg.error === "max_duration") { s.rolling = true; }
      else if (msg.type === "error") { s.error = `Vexa stopped the capture: ${msg.error}`; }
    };
    s.ws.onclose = () => {
      if (s.stopping) return;
      if (s.rolling) {                    // the note's length cap: carry on in a new note, same audio
        s.rolling = false;
        s.base = s.receivedSec;
        if (s.stopping || sessions.get(s.target) !== s) return;
        openNote(s).catch((e) => end(s, String(e && e.message || e)));
        return;
      }
      end(s, s.error || "The connection to Vexa closed.");
    };
  }

  // A promise that gives up after `ms` (the mic prompt can sit unseen in this background tab).
  function within(ms, promise, what) {
    let t;
    return Promise.race([promise, new Promise((_, no) => { t = setTimeout(() => no(new Error(what)), ms); })])
      .finally(() => clearTimeout(t));
  }
  const MIC_WAIT_MS = 3000;

  async function start(target, streamId, label, mode, probe) {
    if (sessions.has(target)) return;
    // The slot is taken BEFORE the first await, so a second click finds it and stops it; after every await,
    // a stop that happened meanwhile wins and nothing (mic, socket, ping) is left running (review of bb128744).
    const s = { target, label, stream: null, part: 0, base: 0, receivedSec: 0, pending: new Float32Array(0), sent: 0,
      mode: mode === "pertrack" && Codec ? "pertrack" : "mix", startedAt: Date.now(), lastPerTrackAt: 0,
      trackMap: new Map(), nextTrack: 1, portGen: 0, selfName: "", probe: probe || null, ptFrames: 0 };
    sessions.set(target, s);
    const stale = () => s.stopping || sessions.get(target) !== s;
    try {
      s.stream = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } }, video: false,
      });
      if (stale()) throw new Error("stopped while starting");
      const stream = s.stream;
      // he still hears the meeting: a captured tab is silenced, so its audio is played back here
      s.out = new Audio(); s.out.srcObject = stream;
      s.ctx = new AudioContext();
      // He must hear the meeting and the capture must run: a context the browser keeps suspended (no
      // click in this tab yet) would record silence. Fail loudly instead (review of bb128744).
      await within(1000, s.ctx.resume(), "resume timed out").catch(() => {});
      if (s.ctx.state !== "running") throw new Error("Click once inside the Vexa panel tab, then click the icon again (the browser has not let it play audio yet).");
      try { await s.out.play(); } catch (e) { throw new Error("The Vexa panel tab cannot play the meeting's audio yet: click once inside it, then click the icon again."); }
      if (stale()) throw new Error("stopped while starting");
      const src = s.ctx.createMediaStreamSource(stream);
      const proc = s.ctx.createScriptProcessor(4096, 1, 1);
      const mute = s.ctx.createGain(); mute.gain.value = 0;
      // the tab carries the others; his own voice comes from his mic (meeting apps never play it back to
      // him). Both are summed into the one stream. The mic is echo-cancelled, and it is never played back.
      const tabGain = s.ctx.createGain(); tabGain.gain.value = 0.8;
      src.connect(tabGain);
      s.tabIntoMix = () => { tabGain.connect(proc); };
      if (s.mode === "mix") s.tabIntoMix();
      else {
        // per speaker: the tab's sound is not sent; it is only watched, for the fallback
        const watch = s.ctx.createAnalyser(); watch.fftSize = 2048; src.connect(watch);
        const buf = new Float32Array(watch.fftSize);
        s.watch = setInterval(() => {
          if (s.mode !== "pertrack") return;      // only per speaker falls back by itself
          watch.getFloatTimeDomainData(buf);
          let peak = 0; for (let i = 0; i < buf.length; i++) { const a = Math.abs(buf[i]); if (a > peak) peak = a; }
          const now = Date.now();
          s.loud = (s.loud || []).filter((t) => now - t < FALLBACK_MS);
          if (peak > 0.02) s.loud.push(now);
          const quietSince = Math.max(s.lastPerTrackAt, s.startedAt, s.reloadedAt || 0);
          if (now - quietSince > FALLBACK_MS && s.loud.length >= FALLBACK_LOUD_SECONDS) fallBack(s, "no per-speaker audio while the meeting played");
          const reloading = now < (s.reloadingUntil || 0);
          if (!reloading && !s.ptPort && s.portGoneAt && now - s.portGoneAt > PORT_GONE_MS) fallBack(s, "the per-speaker link dropped");
        }, 1000);
      }
      const micAsk = navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      try {
        s.mic = await within(MIC_WAIT_MS, micAsk, "mic not granted in time");
        if (stale()) throw new Error("stopped while starting");
        const micSrc = s.ctx.createMediaStreamSource(s.mic);
        const micGain = s.ctx.createGain(); micGain.gain.value = 0.8;
        micSrc.connect(micGain); micGain.connect(proc);
      } catch (e) {
        // a prompt answered after the wait (or after a stop) must not leave the mic on
        micAsk.then((late) => { if (!s.mic || s.mic !== late) late.getTracks().forEach((t) => t.stop()); }).catch(() => {});
        if (stale()) throw e;
        try { s.mic && s.mic.getTracks().forEach((t) => t.stop()); } catch {}
        s.mic = null;
        s.micOff = true;                   // the others are still captured; the pill says his voice is not
      }
      proc.connect(mute); mute.connect(s.ctx.destination);
      const toRate = resampler(s.ctx.sampleRate);
      proc.onaudioprocess = (e) => {
        const x = toRate(e.inputBuffer.getChannelData(0));
        if (!s.ready || !s.ws || s.ws.readyState !== 1) return;
        const all = new Float32Array(s.pending.length + x.length);
        all.set(s.pending); all.set(x, s.pending.length);
        let off = 0;
        if (s.ws.bufferedAmount > MAX_BUFFERED) { end(s, "The connection to Vexa stalled."); return; }
        while (all.length - off >= FRAME) {
          s.ws.send(encode(s.t0 + (s.sent / RATE) * 1000, all.subarray(off, off + FRAME)));
          s.sent += FRAME; off += FRAME;
        }
        s.pending = all.slice(off);
      };
      s.ping = setInterval(() => { try { s.ws && s.ws.readyState === 1 && s.ws.send('{"type":"ping"}'); } catch {} }, 15000);
      stream.getAudioTracks().forEach((t) => t.addEventListener("ended", () => end(s, "")));
      await openNote(s);
      if (stale()) throw new Error("stopped while starting");
      render();
    } catch (e) {
      s.stopping = true;
      try { s.ws && s.ws.close(); } catch {}
      cleanup(s);
      throw e;
    }
  }

  // Per speaker → one stream, for the rest of this capture: the tab's sound joins the mix on track 0 and the
  // per-speaker frames are dropped from here on (never both: every voice would be sent twice).
  function fallBack(s, why) {
    if (s.mode !== "pertrack") return;
    s.fellBack = why;
    s.mode = "mix";
    try { s.tabIntoMix && s.tabIntoMix(); } catch {}
    try { s.ptPort && s.ptPort.disconnect(); } catch {}
    s.ptPort = null;
    chrome.runtime.sendMessage({ type: "vexa-capture-fallback", target: s.target, reason: why }).catch(() => {});
    render();
  }

  function b64ToFloats(b64) {
    const bin = atob(b64);
    if (bin.length % 4 || bin.length / 4 > MAX_PT_SAMPLES || bin.length === 0) return null;
    const u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return new Float32Array(u.buffer);
  }

  // One per-speaker frame from the Zoom tab → one capture.v1 frame on the note's socket.
  function perTrackFrame(s, m) {
    if (s.mode !== "pertrack") return;
    if (!Number.isInteger(m.track) || m.track < 1) return;
    if (typeof m.ts !== "number" || !Number.isFinite(m.ts) || Math.abs(m.ts - Date.now()) > MAX_TS_SKEW_MS) return;
    if (typeof m.pcm !== "string" || m.pcm.length > 90000) return;
    const pcm = b64ToFloats(m.pcm);
    if (!pcm) return;
    // The page numbers its channels from 1 on every (re)start; the note never reuses a track for someone else:
    // each (port, page track) gets the next free note track.
    const key = `${s.portGen}:${m.track}`;
    let track = s.trackMap.get(key);
    if (track === undefined) {
      if (s.nextTrack > MAX_TRACK) { fallBack(s, "more speakers than one note carries"); return; }
      track = s.nextTrack++;
      s.trackMap.set(key, track);
    }
    s.lastPerTrackAt = Date.now();          // only an accepted frame proves per speaker works
    s.ptFrames += 1;
    if (s.ptFrames === 1 || s.ptFrames % 25 === 0) render();
    if (!s.ready || !s.ws || s.ws.readyState !== 1) return;
    if (s.ws.bufferedAmount > MAX_BUFFERED) { end(s, "The connection to Vexa stalled."); return; }
    const name = typeof m.name === "string" && !/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/.test(m.name)
      ? Array.from(m.name.trim()).slice(0, 80).join("") : "";
    s.ws.send(Codec.encodeAudioFrame(track, m.ts, pcm, name || undefined));
  }

  chrome.runtime.onConnect.addListener((port) => {
    const hit = /^vexa-pertrack:(\d+)$/.exec(port.name || "");
    if (!hit) return;
    const s = sessions.get(Number(hit[1]));
    if (!s || s.mode !== "pertrack") { port.disconnect(); return; }
    try { s.ptPort && s.ptPort.disconnect(); } catch {}
    s.ptPort = port;
    s.portGen += 1;
    s.portGoneAt = 0;
    port.onMessage.addListener((m) => {
      if (!m || s.ptPort !== port) return;
      if (m.t === "f") perTrackFrame(s, m);
    });
    port.onDisconnect.addListener(() => { if (s.ptPort === port) { s.ptPort = null; s.portGoneAt = Date.now(); } });
  });

  function cleanup(s) {
    if (sessions.get(s.target) === s) sessions.delete(s.target);
    clearInterval(s.ping);
    clearInterval(s.watch);
    try { s.ptPort && s.ptPort.disconnect(); } catch {}
    s.ptPort = null;
    try { s.stream && s.stream.getTracks().forEach((t) => t.stop()); } catch {}
    try { s.mic && s.mic.getTracks().forEach((t) => t.stop()); } catch {}
    try { s.ctx && s.ctx.state !== "closed" && s.ctx.close().catch(() => {}); } catch {}
    try { if (s.out) { s.out.pause(); s.out.srcObject = null; } } catch {}
    render();
  }

  function stop(s) {
    s.stopping = true;
    try { s.ws && s.ws.readyState === 1 && s.ws.send('{"type":"stop"}'); } catch {}
    setTimeout(() => { try { s.ws && s.ws.close(); } catch {} }, 3000);
    cleanup(s);
  }

  function end(s, error) {
    if (sessions.get(s.target) !== s) return;
    s.stopping = true;
    try { s.ws && s.ws.readyState === 1 && s.ws.send('{"type":"stop"}'); } catch {}
    setTimeout(() => { try { s.ws && s.ws.close(); } catch {} }, 3000);
    cleanup(s);
    chrome.runtime.sendMessage({ type: "vexa-capture-ended", target: s.target, error: error || "" }).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (!msg || !Number.isInteger(msg.target)) return;
    if (msg.type === "vexa-capture-status") { const s = sessions.get(msg.target); reply({ capturing: !!s, mode: s ? s.mode : "", selfName: s ? s.selfName : "" }); return; }
    if (msg.type === "vexa-capture-reloading") {
      const s = sessions.get(msg.target);
      if (s) { s.reloadingUntil = Date.now() + RELOAD_GRACE_MS; s.reloadedAt = Date.now(); }
      reply({ ok: true }); return;
    }
    if (msg.type === "vexa-capture-reloaded") {
      const s = sessions.get(msg.target);
      if (s) { s.reloadingUntil = Date.now() + PORT_GONE_MS; s.reloadedAt = Date.now(); }
      reply({ ok: true }); return;
    }
    if (msg.type === "vexa-capture-mode") { const s = sessions.get(msg.target); if (s && msg.mode === "mix") fallBack(s, String(msg.reason || "").slice(0, 120)); reply({ ok: true }); return; }
    if (msg.type === "vexa-capture-stop") { const s = sessions.get(msg.target); if (s) stop(s); reply({ ok: true }); return; }
    if (msg.type === "vexa-capture-start") {
      const name = msg.mode === "pertrack" ? selfName() : Promise.resolve("");
      start(msg.target, msg.streamId, String(msg.label || "").slice(0, 60), msg.mode, msg.probe)
        .then(async () => {
          const s = sessions.get(msg.target);
          if (s && s.mode === "pertrack") s.selfName = await name;
          reply({ ok: true, selfName: s ? s.selfName : "" });
        })
        .catch((e) => reply({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
  });
  // An extension reload leaves this script running with a dead extension context: its captures would go on
  // playing the meeting back next to the new version's (heard as a heavy echo, 9 Nov 22:09Z). The moment the
  // context is gone, this script ends its own captures (socket, mic, tab stream, playback) and stops.
  const alive = () => { try { return !!(chrome.runtime && chrome.runtime.id); } catch { return false; } };
  const orphanWatch = setInterval(() => {
    if (alive()) return;
    clearInterval(orphanWatch);
    for (const s of [...sessions.values()]) {
      s.stopping = true;
      try { s.ws && s.ws.readyState === 1 && s.ws.send('{"type":"stop"}'); } catch {}
      try { s.ws && s.ws.close(); } catch {}
      try { cleanup(s); } catch {}
    }
    try { pill.remove(); } catch {}
  }, 1000);
  // Unloading ends every capture here: say so, so the meeting tab's REC badge does not outlive it.
  window.addEventListener("beforeunload", () => { for (const s of [...sessions.values()]) end(s, ""); });
  // A freshly loaded panel captures nothing yet: any REC badge no other panel tab owns is stale.
  chrome.runtime.sendMessage({ type: "vexa-panel-loaded" }).catch(() => {});
})();
