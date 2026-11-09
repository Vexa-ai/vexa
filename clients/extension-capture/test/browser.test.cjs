// Hermetic browser test of the capture extension 0.3.0 (per speaker in Zoom's web client and Meet, one stream elsewhere).
// No accounts, no claude-rc, no Zoom: the panel origin, the voice-note API and its socket, and two web pages are
// served by Playwright routes; the "Zoom" page builds two real WebRTC loopback participants (440 Hz, 660 Hz) so
// the hook sees two remote tracks exactly as in Zoom's web client. Every frame the panel sends is decoded here.
// Usage: EXT_ID=<the unpacked id of <extension dir>> CHROME_BIN=<Chromium> node test/browser.test.cjs <playwright-core dir> <extension dir>
// (headed: tabCapture needs a real window; --allowlisted-extension-id lets the worker capture without a click)
const [, , pwDir, ext] = process.argv;
const { chromium } = require(pwDir);
const fs = require("fs"), path = require("path"), os = require("os");
const PANEL = "https://claude-rc-app.dev.vexa.ai";
let failed = 0;
const check = (name, ok, detail = "") => { console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  — " + detail}`); if (!ok) failed++; };

const ZOOM_PAGE = `<!doctype html><title>Zoom meeting</title><body><div id=s>zoom</div><script>
async function participant(freq) {
  const ctx = new AudioContext(); const osc = ctx.createOscillator(); osc.frequency.value = freq;
  const g = ctx.createGain(); g.gain.value = 0.3; const dst = ctx.createMediaStreamDestination();
  osc.connect(g); g.connect(dst); osc.start();
  const a = new RTCPeerConnection(), b = new RTCPeerConnection();
  a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
  b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
  b.ontrack = (e) => { const el = new Audio(); el.srcObject = e.streams[0]; el.play().catch(() => {}); };  // Zoom plays it
  dst.stream.getTracks().forEach((t) => a.addTrack(t, dst.stream));
  await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
}
window.ready = Promise.all([participant(440), participant(660)]).then(() => "ok", (e) => String(e));
</script>`;
// Zoom's real shape (read live 9 Nov 21:4xZ): the whole meeting client runs in ONE same-origin iframe, and the
// peer connections live there, not in the top frame.
const ZOOM_IFRAME_PAGE = "<!doctype html><title>Zoom meeting</title><body><iframe id=c style='width:900px;height:600px' srcdoc=\"" +
  ZOOM_PAGE.replace(/"/g, "&quot;") + "\"></iframe><script>window.ready = new Promise((ok) => { const f = document.getElementById('c'); " +
  "const t = setInterval(() => { try { if (f.contentWindow.ready) { clearInterval(t); f.contentWindow.ready.then(ok); } } catch (e) {} }, 50); });</script>";
// A page that only plays sound (no peer connections): Zoom-like URL, so the press picks per speaker and must fall back.
const SILENT_HOOK_PAGE = `<!doctype html><title>Zoom, audio only</title><script>
window.ready = (async () => { const ctx = new AudioContext(); const o = ctx.createOscillator(); const g = ctx.createGain();
  g.gain.value = 0.3; o.connect(g); g.connect(ctx.destination); o.start(); return ctx.state; })();
</script>`;
const PLAIN_PAGE = SILENT_HOOK_PAGE.replace("Zoom, audio only", "A plain tab");
// Meet: participants are media elements (no WebRTC hook); three of them, each its own oscillator stream.
const MEET_PAGE = `<!doctype html><title>Meet</title><body><script>
window.ready = (async () => { for (const f of [330, 550, 770]) { const ctx = new AudioContext(); const o = ctx.createOscillator();
  o.frequency.value = f; const g = ctx.createGain(); g.gain.value = 0.3; const d = ctx.createMediaStreamDestination();
  o.connect(g); g.connect(d); o.start(); const a = document.createElement("audio"); a.srcObject = d.stream; document.body.appendChild(a);
  await a.play(); } return "ok"; })();
</script>`;
// Meet with no participant elements while the tab plays sound: per speaker carries nothing → falls back.
// Teams: the meeting arrives as ONE server-side mix whose track id starts with "mainAudio" (set here through the
// offer's msid, as Teams' own SDP names it), plus a redundant second track the lane must NOT sum.
const TEAMS_PAGE = `<!doctype html><title>Teams</title><script>
async function track(freq, id) {
  const ctx = new AudioContext(); const osc = ctx.createOscillator(); osc.frequency.value = freq;
  const g = ctx.createGain(); g.gain.value = 0.3; const dst = ctx.createMediaStreamDestination(); osc.connect(g); g.connect(dst); osc.start();
  const a = new RTCPeerConnection(), b = new RTCPeerConnection();
  a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
  b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
  b.ontrack = (e) => { const el = new Audio(); el.srcObject = e.streams[0]; el.play().catch(() => {}); window.got = (window.got || []).concat(e.track.id); };
  dst.stream.getTracks().forEach((t) => a.addTrack(t, dst.stream));
  const offer = await a.createOffer(); await a.setLocalDescription(offer);
  await b.setRemoteDescription({ type: "offer", sdp: offer.sdp.replace(/(a=msid:\\S+ )(\\S+)/g, "$1" + id) });
  await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
}
window.ready = Promise.all([track(440, "mainAudio-1"), track(660, "dominantSpeaker-2")]).then(() => "ok", (e) => String(e));
</script>`;
const MEET_SILENT_PAGE = SILENT_HOOK_PAGE.replace("Zoom, audio only", "Meet, nobody's elements");

function decode(buf) {
  const v = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const raw = v.getInt32(0, true), ts = v.getFloat64(4, true);
  if ((raw & 0x80000000) === 0) return { track: raw, ts, n: (buf.byteLength - 12) / 4 };
  const len = v.getInt32(12, true);
  const name = Buffer.from(buf.buffer, buf.byteOffset + 16, len).toString("utf8");
  const pad = (len + 3) & ~3;
  return { track: raw & 0x7fffffff, ts, name, n: (buf.byteLength - 16 - pad) / 4 };
}


// The panel origin, served for real on 127.0.0.1 over TLS (the browser maps the host here): the panel page,
// POST /api/bots, and the voice-note WebSocket, which decodes every frame the panel sends.
const notes = [];
function startPanelServer() {
  const https = require("https"), crypto = require("crypto");
  // a throwaway self-signed certificate for the panel host, made per run and deleted after it
  const certDir = fs.mkdtempSync(path.join(os.tmpdir(), "vexa-p4-cert-"));
  require("child_process").execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-keyout", path.join(certDir, "k.pem"), "-out", path.join(certDir, "c.pem"), "-subj", "/CN=claude-rc-app.dev.vexa.ai",
    "-addext", "subjectAltName=DNS:claude-rc-app.dev.vexa.ai"], { stdio: "ignore" });
  const tls = { key: fs.readFileSync(path.join(certDir, "k.pem")), cert: fs.readFileSync(path.join(certDir, "c.pem")) };
  fs.rmSync(certDir, { recursive: true, force: true });
  const srv = https.createServer(tls, (req, res) => {
    if (req.url === "/api/auth/me") { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ user: { name: "Test Owner" } })); return; }
    if (req.method === "POST" && req.url === "/api/bots") {
      req.resume(); res.writeHead(201, { "content-type": "application/json" });
      res.end(JSON.stringify({ native_meeting_id: `vn-${notes.length + 1}` })); return;
    }
    res.writeHead(200, { "content-type": "text/html" }); res.end("<!doctype html><title>Vexa panel</title><p>panel");
  });
  srv.on("upgrade", (req, sock) => {
    const accept = crypto.createHash("sha1").update(req.headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest("base64");
    sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
    const note = { url: req.url, frames: [], text: [] }; notes.push(note);
    const sendText = (t) => { const b = Buffer.from(t); sock.write(Buffer.concat([Buffer.from([0x81, b.length]), b])); };
    setTimeout(() => sendText(JSON.stringify({ type: "ready" })), 100);
    let buf = Buffer.alloc(0);
    sock.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      for (;;) {
        if (buf.length < 2) return;
        const op = buf[0] & 0x0f; let len = buf[1] & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (buf.length < off + 4 + len) return;
        const mask = buf.subarray(off, off + 4); const data = Buffer.alloc(len);
        for (let i = 0; i < len; i++) data[i] = buf[off + 4 + i] ^ mask[i % 4];
        buf = buf.subarray(off + 4 + len);
        if (op === 1) note.text.push(data.toString("utf8"));
        else if (op === 2) note.frames.push(decode(data));
        else if (op === 8) { try { sock.end(); } catch {} return; }
      }
    });
    sock.on("error", () => {});
  });
  return new Promise((ok) => srv.listen(0, "127.0.0.1", () => ok(srv)));
}

(async () => {
  const srv = await startPanelServer();
  const port = srv.address().port;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "vexa-p4-"));
  const ctx = await chromium.launchPersistentContext(profile, {
    headless: false, executablePath: process.env.CHROME_BIN || undefined,
    args: [...(process.env.HEADLESS_NEW ? ["--headless=new"] : []), `--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--allowlisted-extension-id=${process.env.EXT_ID}`,
      "--autoplay-policy=no-user-gesture-required", `--host-resolver-rules=MAP claude-rc-app.dev.vexa.ai:443 127.0.0.1:${port}`, "--ignore-certificate-errors", "--use-fake-device-for-media-stream"],
    viewport: { width: 1000, height: 700 },
  });
  await ctx.grantPermissions(["microphone"], { origin: PANEL });
  await ctx.route("https://app.zoom.us/wc/111/join", (r) => r.fulfill({ contentType: "text/html", body: ZOOM_PAGE }));
  await ctx.route("https://app.zoom.us/wc/333/join", (r) => r.fulfill({ contentType: "text/html", body: ZOOM_IFRAME_PAGE }));
  await ctx.route("https://app.zoom.us/wc/222/join", (r) => r.fulfill({ contentType: "text/html", body: SILENT_HOOK_PAGE }));
  await ctx.route("https://plain.example/", (r) => r.fulfill({ contentType: "text/html", body: PLAIN_PAGE }));
  await ctx.route("https://meet.google.com/abc-defg-hij", (r) => r.fulfill({ contentType: "text/html", body: MEET_PAGE }));
  await ctx.route("https://teams.microsoft.com/v2/", (r) => r.fulfill({ contentType: "text/html", body: TEAMS_PAGE }));
  await ctx.route("https://meet.google.com/kkk-llll-mmm", (r) => r.fulfill({ contentType: "text/html", body: MEET_SILENT_PAGE }));
  try {
    const sw = ctx.serviceWorkers()[0] || await ctx.waitForEvent("serviceworker", { timeout: 15000 });
    const panel = await ctx.newPage(); await panel.goto(`${PANEL}/panel`); await panel.waitForTimeout(800);
    await panel.mouse.click(50, 50);       // one click in the panel tab, as he does (lets it play the meeting back)

    const press = (urlPart) => sw.evaluate(async (u) => {
      const tabs = await chrome.tabs.query({});
      const t = tabs.find((x) => (x.url || "").includes(u)) || tabs.sort((a, b) => b.id - a.id)[0];
      return { ...(await self.vexaToggle(t)), tabId: t.id };
    }, urlPart);

    // 1 · Zoom web client with two participants → per speaker
    const zoom = await ctx.newPage(); await zoom.goto("https://app.zoom.us/wc/111/join");
    check("the fake Zoom page set up two WebRTC participants", (await zoom.evaluate(() => window.ready)) === "ok");
    await zoom.waitForTimeout(1500);
    const hookSeen = await zoom.evaluate(() => ({ hooked: window.__vexaRemoteAudioHookInstalled === true,
      streams: (window.__vexaCapturedRemoteAudioStreams || []).length,
      muted: [...document.querySelectorAll("audio[data-vexa-injected]")].every((a) => a.muted && a.volume === 0) }));
    check("the hook ran before Zoom's peer connections and holds both remote tracks", hookSeen.hooked && hookSeen.streams === 2, JSON.stringify(hookSeen));
    check("the hook's mirror elements are muted (he never hears a voice twice)", hookSeen.muted, JSON.stringify(hookSeen));
    const before = await zoom.evaluate(() => typeof window.VexaPageCapture);
    check("nothing is captured before the press (only the hook is in the page)", before === "object" && notes.length === 0);
    const r1 = await press("app.zoom.us/wc/111");
    check("the press in Zoom runs per speaker", r1.ok && r1.mode === "pertrack", JSON.stringify(r1));
    await panel.waitForTimeout(4000);
    const n1 = notes[0] || { frames: [] };
    const tracks1 = [...new Set(n1.frames.map((f) => f.track))].sort();
    check("the note gets his mic on track 0 and each participant on its own track (1, 2)", JSON.stringify(tracks1) === "[0,1,2]", JSON.stringify(tracks1));
    const pt = n1.frames.filter((f) => f.track > 0);
    const now = Date.now();
    check("per-speaker frames are 4096 samples stamped on the wall clock", pt.length > 4 && pt.every((f) => f.n === 4096 && Math.abs(f.ts - now) < 10000), JSON.stringify(pt.slice(0, 2)));
    const pill = await panel.evaluate(() => document.querySelector("[data-vexa-capture]")?.textContent || "");
    check("the panel pill says per speaker", /per speaker/.test(pill), pill);
    check("the pill states the page's capture facts (hook, connections, tracks, speaker frames)",
      /zoom: hook ✓ · \d+ conn · 2 tracks \(2 live\)/.test(pill) && /[1-9][0-9]* speaker frames/.test(pill), pill);
    // a reload of the Zoom page mid-capture: the new page is re-asked and stays per speaker, on fresh tracks
    await zoom.reload(); await zoom.evaluate(() => window.ready);
    await panel.waitForTimeout(9000);
    const stR = await sw.evaluate(async (id) => {
      const p = (await chrome.tabs.query({ url: "https://claude-rc-app.dev.vexa.ai/*" }))[0];
      return chrome.tabs.sendMessage(p.id, { type: "vexa-capture-status", target: id });
    }, r1.tabId);
    const tracksR = [...new Set(n1.frames.map((f) => f.track))].sort();
    check("after a reload of the Zoom page it is still per speaker (no fallback)", stR.capturing && stR.mode === "pertrack", JSON.stringify(stR));
    check("…and the new page's speakers get fresh note tracks (3, 4), never someone else's", JSON.stringify(tracksR) === "[0,1,2,3,4]", JSON.stringify(tracksR));
    const r1s = await press("app.zoom.us/wc/111");
    await panel.waitForTimeout(800);
    const after = n1.frames.length; await panel.waitForTimeout(1500);
    check("the second press stops it: no frame after the stop", r1s.ok && r1s.stopped && n1.frames.length === after && n1.text.some((t) => t.includes('"stop"')), `${after}→${n1.frames.length}`);
    await zoom.close();

    // 1b · the meeting inside a same-origin iframe (Zoom's web client): the hook runs in that frame, per speaker works
    const zf = await ctx.newPage(); await zf.goto("https://app.zoom.us/wc/333/join");
    check("the fake Zoom iframe page set up two participants inside its iframe", (await zf.evaluate(() => window.ready)) === "ok");
    await zf.waitForTimeout(1500);
    const inFrame = await zf.evaluate(() => { const w = document.getElementById("c").contentWindow;
      return { top: (window.__vexaCapturedRemoteAudioStreams || []).length, frame: (w.__vexaCapturedRemoteAudioStreams || []).length, hooked: w.__vexaRemoteAudioHookInstalled === true }; });
    check("the hook ran inside the meeting iframe and holds both tracks there", inFrame.hooked && inFrame.frame === 2 && inFrame.top === 0, JSON.stringify(inFrame));
    const rf = await press("app.zoom.us/wc/333");
    check("the press drives the iframe and runs per speaker", rf.ok && rf.mode === "pertrack", JSON.stringify(rf));
    await panel.waitForTimeout(4000);
    const nf = notes[notes.length - 1] || { frames: [] };
    const tf = [...new Set(nf.frames.map((f) => f.track))].sort();
    check("iframe meeting: his mic on track 0 and each participant on its own track", JSON.stringify(tf) === "[0,1,2]", JSON.stringify(tf));
    await press("app.zoom.us/wc/333"); await zf.close();

    // 2 · a plain tab → one stream (0.2.3), track 0 only
    const plain = await ctx.newPage(); await plain.goto("https://plain.example/"); await plain.evaluate(() => window.ready);
    const before2 = notes.length;
    const [r2, r2b] = await Promise.all([press("plain.example"), press("plain.example")]);   // a double click
    await panel.waitForTimeout(1500);
    check("a double press opens ONE note (the second is ignored while the first starts)", notes.length === before2 + 1 && (r2b.error === "busy" || r2.error === "busy"), JSON.stringify({ opened: notes.length - before2, r2b }));
    if (r2.error === "busy") Object.assign(r2, r2b);
    check("the press in a non-Zoom tab runs as one stream", r2.ok && r2.mode === "mix", JSON.stringify(r2));
    await panel.waitForTimeout(3000);
    const n2 = notes[notes.length - 1] || { frames: [] };
    check("one stream sends track 0 only", n2.frames.length > 2 && n2.frames.every((f) => f.track === 0), JSON.stringify([...new Set(n2.frames.map((f) => f.track))]));
    await press("plain.example"); await plain.close();

    // 3 · a Zoom tab whose hook holds no remote track yet → «named»: the tab's audio on track 1, named by the
    //     page's active-speaker edges (posted here as the page would; the timeline itself is unit-tested)
    const quiet = await ctx.newPage(); await quiet.goto("https://app.zoom.us/wc/222/join"); await quiet.evaluate(() => window.ready);
    const r3 = await press("app.zoom.us/wc/222");
    check("a Zoom tab with no remote track yet runs as one stream (no named fallback)", r3.ok && r3.mode === "mix", JSON.stringify(r3));
    await press("app.zoom.us/wc/222"); await quiet.close();

    // 4 · Google Meet: participants from their media elements, the bot's own Meet capture
    const meet = await ctx.newPage(); await meet.goto("https://meet.google.com/abc-defg-hij");
    check("the fake Meet page plays three participants", (await meet.evaluate(() => window.ready)) === "ok");
    const meetHook = await meet.evaluate(() => ({ hooked: window.__vexaRemoteAudioHookInstalled === true, utils: typeof window.VexaPageCapture }));
    check("Meet gets the page capture but no WebRTC hook (its mirrors would double every voice)", !meetHook.hooked && meetHook.utils === "object", JSON.stringify(meetHook));
    const r4 = await press("meet.google.com/abc");
    check("the press in Meet runs per speaker", r4.ok && r4.mode === "pertrack", JSON.stringify(r4));
    await panel.waitForTimeout(5000);
    const n4 = notes[notes.length - 1] || { frames: [] };
    const t4 = [...new Set(n4.frames.map((f) => f.track))].sort();
    check("Meet: his mic on track 0 and the three participants on tracks 1–3", JSON.stringify(t4) === "[0,1,2,3]", JSON.stringify(t4));
    await press("meet.google.com/abc"); await meet.close();

    // 4b · Teams: one server mix on track 1 (only the mainAudio track summed), his mic on track 0
    const teams = await ctx.newPage(); await teams.goto("https://teams.microsoft.com/v2/");
    check("the fake Teams page receives a mainAudio track and a redundant one", (await teams.evaluate(() => window.ready)) === "ok"
      && JSON.stringify(await teams.evaluate(() => (window.got || []).sort())) === '["dominantSpeaker-2","mainAudio-1"]', JSON.stringify(await teams.evaluate(() => window.got)));
    await teams.waitForTimeout(1000);
    const r4b = await press("teams.microsoft.com");
    check("the press in Teams runs per speaker (the mixed lane)", r4b.ok && r4b.mode === "pertrack", JSON.stringify(r4b));
    await panel.waitForTimeout(5000);
    const n4b = notes[notes.length - 1] || { frames: [] };
    const t4b = [...new Set(n4b.frames.map((f) => f.track))].sort();
    check("Teams: his mic on track 0 and the meeting mix on track 1", JSON.stringify(t4b) === "[0,1]", JSON.stringify(t4b));
    const mixedSeen = await teams.evaluate(() => (window.__vexaCapturedRemoteAudioStreams || []).length);
    check("…the hook holds both Teams tracks (the pick of mainAudio is pinned by mixed-lane-capture.test.ts)", mixedSeen === 2);
    await press("teams.microsoft.com"); await teams.close();

    // 5 · Meet with no participant elements while the tab plays sound → falls back to one stream by itself
    const ms = await ctx.newPage(); await ms.goto("https://meet.google.com/kkk-llll-mmm"); await ms.evaluate(() => window.ready);
    const r5 = await press("meet.google.com/kkk");
    check("the press there starts per speaker", r5.ok && r5.mode === "pertrack", JSON.stringify(r5));
    await panel.waitForTimeout(25000);
    const st = await sw.evaluate(async (id) => {
      const p = (await chrome.tabs.query({ url: "https://claude-rc-app.dev.vexa.ai/*" }))[0];
      return chrome.tabs.sendMessage(p.id, { type: "vexa-capture-status", target: id });
    }, r5.tabId);
    check("after 20 s of sustained tab sound with no per-speaker audio it is one stream", st.capturing && st.mode === "mix", JSON.stringify(st));
    check("his name from the panel profile reached the page capture", st.selfName === "Test Owner", JSON.stringify(st));
    await press("meet.google.com/kkk");
    // 6 · an extension reload in the middle of a capture: the old panel script must end its capture by itself
    //     (it went on playing the meeting next to the new version's: a heavy echo, 9 Nov 22:09Z)
    const pl6 = await ctx.newPage(); await pl6.goto("https://plain.example/"); await pl6.evaluate(() => window.ready);
    const r6 = await press("plain.example");
    await panel.waitForTimeout(2500);
    const n6 = notes[notes.length - 1] || { frames: [], text: [] };
    const before6 = n6.frames.length;
    await sw.evaluate(() => { setTimeout(() => chrome.runtime.reload(), 50); return true; }).catch(() => {});
    await panel.waitForTimeout(3500);
    const mid6 = n6.frames.length; await panel.waitForTimeout(2000);
    const pillGone = await panel.evaluate(() => !document.querySelector("[data-vexa-capture]") || document.querySelector("[data-vexa-capture]").style.display === "none").catch(() => null);
    check("after an extension reload the old capture ends by itself: stop sent, no frame after, no pill",
      r6.ok && before6 > 0 && n6.text.some((t) => t.includes('"stop"')) && n6.frames.length === mid6 && pillGone !== false,
      JSON.stringify({ before6, mid6, after: n6.frames.length, stop: n6.text.some((t) => t.includes('"stop"')), pillGone }));
  } catch (e) {
    check("no exception", false, String(e && e.stack || e));
  } finally {
    await ctx.close();
    srv.close();
    fs.rmSync(profile, { recursive: true, force: true });
  }
  console.log(failed ? `\n${failed} FAILED` : "\ncapture extension 0.3.0: all passed");
  process.exit(failed ? 1 : 0);
})();
