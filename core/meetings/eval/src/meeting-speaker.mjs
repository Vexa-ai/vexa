// A Jitsi "human" — headless Chromium joins a room with a WAV as its microphone.
// Provides real WebRTC audio with known ground truth, no TTS service and no host needed.
//   ROOM=<room> NAME=<display name> WAV=/audio/x.wav [PROBE=1] node jitsi-speaker.mjs
const { chromium } = await import(new URL("../../modules/join/node_modules/playwright/index.mjs", import.meta.url).href);

const ROOM = process.env.ROOM;
const NAME = process.env.NAME ?? 'Speaker';
const WAV = process.env.WAV;
const HOLD_MS = Number(process.env.HOLD_MS ?? 130000);
const BASE = process.env.JITSI_BASE ?? 'https://meet.jit.si';
const PROBE = process.env.PROBE === '1';

const url = `${BASE}/${ROOM}#config.prejoinPageEnabled=false&config.startWithAudioMuted=false&config.startWithVideoMuted=true&userInfo.displayName=%22${encodeURIComponent(NAME)}%22`;

const args = [
  '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream',
  '--autoplay-policy=no-user-gesture-required',
  '--disable-dev-shm-usage',
  '--no-sandbox',
  // harness-only: the lab CA is private. The BOT trusts it properly via NSS instead.
  '--ignore-certificate-errors',
];
if (WAV) args.push(`--use-file-for-fake-audio-capture=${WAV}%noloop`);

const ctx = await chromium.launchPersistentContext('', {
  headless: false,
  args,
  permissions: ['microphone', 'camera'],
});
const page = ctx.pages()[0] ?? await ctx.newPage();
page.on('console', (m) => { if (PROBE) console.log(`[page] ${m.text().slice(0, 160)}`); });

console.log(`[speaker:${NAME}] joining ${url}`);
await page.goto(url, { timeout: 90000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(8000);

// Dismiss whatever pre-join / consent UI the deployment shows.
for (const sel of ['button:has-text("Join meeting")', 'button:has-text("Join")', '[data-testid="prejoin.joinMeeting"]', 'button:has-text("I agree")', 'button:has-text("Continue")']) {
  const el = page.locator(sel).first();
  if (await el.isVisible({ timeout: 1500 }).catch(() => false)) {
    console.log(`[speaker:${NAME}] clicking ${sel}`);
    await el.click().catch(() => {});
    await page.waitForTimeout(2500);
  }
}
await page.waitForTimeout(5000);

const state = await page.evaluate(() => ({
  url: location.href,
  title: document.title,
  body: document.body.innerText.slice(0, 400),
  inConference: !!document.querySelector('#largeVideoContainer, .videocontainer, [class*="conference"]'),
  participants: document.querySelectorAll('[id^="participant_"]').length,
}));
console.log(`[speaker:${NAME}] state: ${JSON.stringify(state, null, 2)}`);

if (PROBE) { await ctx.close(); process.exit(state.inConference ? 0 : 3); }

// Unmute if Jitsi joined muted, then hold the call open while the WAV plays through.
const unmute = page.locator('[aria-label*="Unmute"], [data-testid="toolbox.mute"]').first();
if (await unmute.isVisible({ timeout: 2000 }).catch(() => false)) await unmute.click().catch(() => {});
console.log(`[speaker:${NAME}] holding ${HOLD_MS}ms`);

// ADMIT=1 makes this participant the room's doorkeeper. meet.jit.si puts a joining bot in the
// lobby — even in an empty room — and a bot cannot admit itself, which would put a human in the
// loop for every autonomous multi-party run. The FIRST participant is moderator, so a speaker that
// joined first can let the bot in. Polling beats a one-shot wait: the knock arrives whenever the
// bot's own join finishes, which is not a time this script can predict.
const ADMIT = process.env.ADMIT === '1';
const deadline = Date.now() + HOLD_MS;
let admitted = 0;
while (Date.now() < deadline) {
  if (ADMIT) {
    const btn = page.locator('[data-testid="lobby.allow"], [aria-label*="Admit"], button:has-text("Admit")').first();
    if (await btn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await btn.click().catch(() => { /* the knock may vanish between seeing and clicking */ });
      console.log(`[speaker:${NAME}] ADMITTED a lobby knock (${++admitted} total)`);
    }
  }
  await page.waitForTimeout(2000);
}
await ctx.close();
console.log(`[speaker:${NAME}] done`);
