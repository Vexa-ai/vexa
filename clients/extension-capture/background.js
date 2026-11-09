// Vexa capture: the toolbar press on a meeting tab hands that tab's audio to an open Vexa panel tab.
// The panel tab (panel-capture.js, running as the panel's own origin with his session) makes the voice note
// and streams it. This worker holds no credential and opens no socket: it asks Chrome for the tab's capture id,
// with the panel tab as the one allowed to consume it (tabCapture consumerTabId), and, in a Zoom web-client tab,
// relays the per-speaker frames of the bot's own Zoom capture (zoom-main.js) from that tab to the panel tab.
// Two modes, chosen per press:
//   per speaker: Zoom's web client or Google Meet → each participant on its own track (1..), named by the
//                bot's own resolver; Teams → its one server mix on track 1 (Teams sends the meeting as one mix). The bot's own capture for each; his mic on track 0. The tab's audio is still taken, only to play it to him
//                and to watch for the fallback.
//   one stream:  any other tab, or a Zoom tab opened before the extension → the tab's audio + his mic summed on
//                track 0 (0.2.3). Per speaker falls back to this by itself when it carries nothing (panel-capture.js).
const PANEL = "https://claude-rc-app.dev.vexa.ai/*";

async function mark(tabId, state, title) {
  const text = state === "on" ? "REC" : state === "error" ? "!" : "";
  await chrome.action.setBadgeText({ tabId, text });
  if (text) await chrome.action.setBadgeBackgroundColor({ tabId, color: state === "on" ? "#d93025" : "#b06000" });
  await chrome.action.setTitle({ tabId, title: title || (state === "on"
    ? "Vexa is capturing this tab. Click to stop."
    : "Vexa: capture this tab") });
}

// A panel tab whose capture script answers. After an extension reload a panel tab still holds the old (dead)
// script: the worker injects the current one itself, so no tab needs a manual reload (founder, 9 Nov 21:3xZ).
async function answers(tabId) {
  try { const r = await chrome.tabs.sendMessage(tabId, { type: "vexa-capture-status", target: -1 }); return !!r; } catch { return false; }
}
async function panelTab(exceptId) {
  const tabs = (await chrome.tabs.query({ url: PANEL })).filter((t) => t.id !== exceptId && !t.discarded)
    .sort((a, b) => (b.active - a.active) || (b.lastAccessed || 0) - (a.lastAccessed || 0));
  for (const t of tabs) if (await answers(t.id)) return t;
  for (const t of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["vendor/vexa-capture-codec.js", "panel-capture.js"] });
      if (await answers(t.id)) return t;
    } catch { /* not injectable: try the next */ }
  }
  return tabs[0] || null;
}

// The meeting tab's page capture, injected now when the tab predates this extension load. Too late for the
// WebRTC hook (per speaker needs a fresh page): the press then says «reload the meeting tab».
async function ensurePageCapture(tabId) {
  const p = await askZoom(tabId, { type: "vexa-pertrack-probe" });
  if (p) return p;
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, world: "MAIN", files: ["vendor/vexa-page-capture.js", "page-main.js"] });
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["page-relay.js"] });
  } catch { return null; }                 // not a meeting site we may run on: one stream
  return askZoom(tabId, { type: "vexa-pertrack-probe" });
}

async function ask(panelId, message) {
  try {
    return await chrome.tabs.sendMessage(panelId, message);
  } catch (e) {
    return { ok: false, error: "The Vexa panel tab is not ready. Reload it, then click again." };
  }
}

// The meeting tab's relay (page-relay.js), top frame only. No relay there (not Zoom's web client or Meet, or a tab
// opened before the extension loaded) → null, and the press runs as one stream.
async function askFrame(tabId, frameId, message) {
  try { return await chrome.tabs.sendMessage(tabId, message, { frameId }); } catch { return null; }
}
const callFrame = new Map();              // tab id → the frame that holds the call (re-probed when unknown)
async function probeFrames(tabId) {
  let frames = [{ frameId: 0 }];
  try { frames = (await chrome.webNavigation.getAllFrames({ tabId })) || frames; } catch { /* top only */ }
  let best = null;
  // all frames at once (a frame without the page capture costs its 1.5 s wait only once, in parallel)
  const answers = await Promise.all(frames.map(async (f) => ({ f, p: await askFrame(tabId, f.frameId, { type: "vexa-pertrack-probe" }) })));
  for (const { f, p } of answers) {
    if (!p || p.absent) continue;
    // live tracks first, then hooked tracks, connections, a ready capture; a tie goes to the top frame (review of b44b0676)
    const score = (p.streams | 0) * 1000 + (p.hooked | 0) * 10 + (p.pcs | 0) + (p.ready ? 5 : 0) + (f.frameId === 0 ? 0.5 : 0);
    if (!best || score > best.score) best = { frameId: f.frameId, probe: p, score };
  }
  if (best) callFrame.set(tabId, best.frameId);
  return best;
}
// The meeting tab's relay in the frame that holds the call (Zoom: an iframe). No relay → null: one stream.
async function askZoom(tabId, message) {
  if (message && message.type === "vexa-pertrack-probe") { const b = await probeFrames(tabId); return b ? b.probe : null; }
  if (message && message.type === "vexa-pertrack-stop") {   // stop every frame's side
    let frames = [{ frameId: 0 }];
    try { frames = (await chrome.webNavigation.getAllFrames({ tabId })) || frames; } catch { /* top only */ }
    for (const f of frames) await askFrame(tabId, f.frameId, message);
    return { ok: true };
  }
  if (!callFrame.has(tabId)) await probeFrames(tabId);
  return askFrame(tabId, callFrame.get(tabId) ?? 0, message);
}

async function startPerTrack(target, panelId, selfName) {
  const z = await askZoom(target, { type: "vexa-pertrack-start", selfName: selfName || "" });
  if (z && z.ok) return true;
  await ask(panelId, { type: "vexa-capture-mode", target, mode: "mix", reason: (z && z.error) || "no per-speaker capture in this tab" });
  return false;
}

async function toggle(tab) {
  const panel = await panelTab(tab.id);
  if (!panel) {
    await mark(tab.id, "error", "Open your Vexa panel (claude-rc-app.dev.vexa.ai) in another tab, then click again.");
    return { ok: false, error: "no panel tab" };
  }
  // Stop from whichever panel tab is capturing this tab (there may be two panel tabs open).
  const owner = await capturingPanel(tab.id);
  if (owner) {
    await askZoom(tab.id, { type: "vexa-pertrack-stop" });
    await ask(owner.id, { type: "vexa-capture-stop", target: tab.id });
    await mark(tab.id, "off");
    return { ok: true, stopped: true };
  }
  const probe = await ensurePageCapture(tab.id);
  // Zoom/Teams: only once the hook holds a remote track (pressed before joining audio → one stream); Meet: its elements.
  // A WebRTC meeting page whose hook came in after the page connected holds no tracks and never will: the tab
  // must be reloaded with the extension in place (founder, 9 Nov: say it, don't silently run one stream).
  if (probe && probe.needsTracks && probe.late && !(probe.streams > 0)) {
    await mark(tab.id, "error", "Reload this meeting tab (rejoin), then click again: the capture has to be in the page before it connects.");
    return { ok: false, error: "reload the meeting tab" };
  }
  // per speaker, or the plain single stream as the last resort (founder, 9 Nov 21:4xZ: «no fallback please»)
  const mode = probe && probe.ready && (!probe.needsTracks || probe.streams > 0) ? "pertrack" : "mix";
  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id, consumerTabId: panel.id });
  } catch (e) {
    await mark(tab.id, "error", `Chrome refused to capture this tab: ${e && e.message ? e.message : e}`);
    return { ok: false, error: "tabCapture refused" };
  }
  const r = await ask(panel.id, { type: "vexa-capture-start", target: tab.id, streamId, label: tab.title || "", mode, probe: probe || { absent: true } });
  if (!(r && r.ok)) {
    await mark(tab.id, "error", (r && r.error) || "Vexa could not start the capture.");
    return { ok: false, error: (r && r.error) || "start failed" };
  }
  await mark(tab.id, "on");
  const page = mode !== "mix" ? await startPerTrack(tab.id, panel.id, r.selfName) : false;
  return { ok: true, mode: page ? mode : "mix" };
}

const pressing = new Set();             // tab ids with a press in flight (a double click must not open two notes)
async function press(tab) {
  if (pressing.has(tab.id)) return { ok: false, error: "busy" };
  pressing.add(tab.id);
  try { return await toggle(tab); } finally { pressing.delete(tab.id); }
}
chrome.action.onClicked.addListener((tab) => { press(tab).catch(() => {}); });
self.vexaToggle = press;   // the same press, for test/browser.test.cjs (callable only from inside this worker)

// Which open panel tab, if any, is capturing `targetId` right now, and in which mode (asked, never remembered:
// the worker may have been suspended since the capture started).
async function capturingPanel(targetId) {
  const panels = await chrome.tabs.query({ url: PANEL });
  for (const p of panels) {
    if (p.id === targetId) continue;
    const r = await ask(p.id, { type: "vexa-capture-status", target: targetId });
    if (r && r.capturing) return { id: p.id, mode: r.mode || "mix", selfName: r.selfName || "" };
  }
  return null;
}

// The per-speaker frames: one port from the Zoom tab's relay, piped to one port into the panel tab that captures
// that tab. Only this extension's own content scripts can open it; the Zoom tab is the port's sender tab.
chrome.runtime.onConnect.addListener((zoom) => {
  if (zoom.name !== "vexa-pertrack" || !zoom.sender || !zoom.sender.tab) return;
  if (callFrame.has(zoom.sender.tab.id) && callFrame.get(zoom.sender.tab.id) !== zoom.sender.frameId) { try { zoom.disconnect(); } catch { /* gone */ } return; }
  const target = zoom.sender.tab.id;
  const queue = [];
  let panelPort = null, closed = false;
  zoom.onMessage.addListener((m) => {
    if (panelPort) { try { panelPort.postMessage(m); } catch { /* closing */ } }
    else if (queue.length < 64) queue.push(m);
  });
  zoom.onDisconnect.addListener(() => { closed = true; try { if (panelPort) panelPort.disconnect(); } catch { /* gone */ } });
  capturingPanel(target).then((owner) => {
    if (closed) return;
    if (!owner || owner.mode !== "pertrack") { try { zoom.disconnect(); } catch { /* gone */ } return; }
    panelPort = chrome.tabs.connect(owner.id, { name: `vexa-pertrack:${target}` });
    panelPort.onDisconnect.addListener(() => { try { zoom.disconnect(); } catch { /* gone */ } });
    for (const m of queue.splice(0)) panelPort.postMessage(m);
  }).catch(() => { try { zoom.disconnect(); } catch { /* gone */ } });
});

// A REC badge only while a capture really runs (review of 416d08eb, two lows):
//  - a panel tab that reloads drops its captures: it says so on load, and every REC no panel still owns is cleared;
//  - Chrome resets a tab's badge when the tab navigates (Zoom's web client moves between /wc/ pages while the
//    capture goes on): after each load the badge is put back if a panel tab is still capturing that tab, and a
//    per-speaker capture is re-asked of the new page (its scripts are new; the old page's taps are gone).
async function clearStaleBadges() {
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) {
    let text = "";
    try { text = await chrome.action.getBadgeText({ tabId: t.id }); } catch { continue; }
    if (text === "REC" && !(await capturingPanel(t.id))) await mark(t.id, "off").catch(() => {});
  }
}

chrome.tabs.onRemoved.addListener((tabId) => { callFrame.delete(tabId); });
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status !== "complete" && info.status !== "loading") return;
  capturingPanel(tabId).then(async (p) => {
    if (!p) return;
    if (info.status === "loading") {
      callFrame.delete(tabId);
      // the meeting page is being replaced: its per-speaker link will drop; the panel waits for the new page
      if (p.mode === "pertrack") await ask(p.id, { type: "vexa-capture-reloading", target: tabId });
      return;
    }
    await mark(tabId, "on");
    if (p.mode === "pertrack") {
      for (let i = 0; i < 10; i++) {
        const b = await probeFrames(tabId);
        if (b && (b.probe.streams | 0) > 0) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
      await ask(p.id, { type: "vexa-capture-reloaded", target: tabId });
      await startPerTrack(tabId, p.id, p.selfName);
    }
  }).catch(() => {});
});

// the panel tab says a capture ended on its own (the meeting tab closed, the network dropped, the panel unloaded),
// that it fell back to one stream, or that it has just loaded (so it captures nothing yet)
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "vexa-capture-ended" && Number.isInteger(msg.target)) {
    askZoom(msg.target, { type: "vexa-pertrack-stop" }).catch(() => {});
    mark(msg.target, msg.error ? "error" : "off", msg.error || undefined).catch(() => {});
  } else if (msg && msg.type === "vexa-capture-fallback" && Number.isInteger(msg.target)) {
    askZoom(msg.target, { type: "vexa-pertrack-stop" }).catch(() => {});
  } else if (msg && msg.type === "vexa-panel-loaded") {
    clearStaleBadges().catch(() => {});
  }
});
