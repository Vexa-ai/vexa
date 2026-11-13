/**
 * AIM-2050 — virtual camera. Node-side: avatar fetch → data: URI, and the Google-Meet-only gate.
 * Browser-side (real headless Chromium, green-or-skip like the other boundary tests): the init
 * script answers getUserMedia({video}) with a canvas track that shows the avatar, lists a camera
 * in enumerateDevices, and swaps outgoing video in addTrack / replaceTrack.
 * Run: npx tsx src/virtual-camera.test.ts
 */
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { launchPersistentBrowser, type BrowserContext } from '@vexa/remote-browser';
import { buildVirtualCameraInitScript, resolveAvatarDataUri, wantsVirtualCamera } from './virtual-camera.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const BLUE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#004CFA"/></svg>';
const fakeFetch = (body: string | Buffer, type: string, status = 200): typeof fetch =>
  (async () => new Response(body, { status, headers: { 'content-type': type } })) as typeof fetch;

async function nodeSide(): Promise<void> {
  const svg = await resolveAvatarDataUri('https://x/logo.svg', fakeFetch(BLUE_SVG, 'image/svg+xml'));
  check('svg → data:image/svg+xml URI', svg === `data:image/svg+xml;base64,${Buffer.from(BLUE_SVG).toString('base64')}`, String(svg).slice(0, 60));
  check('data: URI passes through', (await resolveAvatarDataUri('data:image/png;base64,AA')) === 'data:image/png;base64,AA');
  check('non-image → null', (await resolveAvatarDataUri('https://x/', fakeFetch('<html>', 'text/html'))) === null);
  check('HTTP 404 → null', (await resolveAvatarDataUri('https://x/', fakeFetch('', 'image/png', 404))) === null);
  check('too large → null', (await resolveAvatarDataUri('https://x/', fakeFetch(Buffer.alloc(1_000_001), 'image/png'))) === null);
  const throwing = (async () => { throw new Error('ECONNREFUSED'); }) as unknown as typeof fetch;
  check('unreachable → null', (await resolveAvatarDataUri('https://x/', throwing)) === null);

  check('gate: google_meet + avatar', wantsVirtualCamera({ platform: 'google_meet', defaultAvatarUrl: 'https://x/a.svg' }));
  check('gate: no avatar → off', !wantsVirtualCamera({ platform: 'google_meet' }));
  check('gate: teams → off (AIM-2065: publishing video drops the Teams call)', !wantsVirtualCamera({ platform: 'teams', defaultAvatarUrl: 'https://x/a.svg' }));
  check('gate: zoom + avatar', wantsVirtualCamera({ platform: 'zoom', defaultAvatarUrl: 'https://x/a.svg' }));
  check('gate: jitsi → off', !wantsVirtualCamera({ platform: 'jitsi', defaultAvatarUrl: 'https://x/a.svg' }));
}

/** Center + corner pixel of what the camera track shows, read back through a <video>. */
const PROBE = `(async () => {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const s = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  const v = document.createElement('video');
  v.muted = true; v.srcObject = s; document.body.appendChild(v); await v.play();
  await new Promise((r) => setTimeout(r, 700));
  const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
  const x = c.getContext('2d'); x.drawImage(v, 0, 0);
  const px = (a, b) => Array.from(x.getImageData(a, b, 1, 1).data.slice(0, 3));
  const other = document.createElement('canvas').captureStream().getVideoTracks()[0];
  const pc = new RTCPeerConnection();
  const sender = pc.addTrack(other, new MediaStream([other]));
  const swappedOnAdd = sender.track !== other && sender.track.kind === 'video';
  const other2 = document.createElement('canvas').captureStream().getVideoTracks()[0];
  await sender.replaceTrack(other2);
  const swappedOnReplace = sender.track !== other2;
  pc.close();
  // Teams light meetings: an audio-only connection must still offer a sending video line.
  const pc2 = new RTCPeerConnection();
  pc2.addTransceiver('audio', { direction: 'recvonly' });
  const sdp = (await pc2.createOffer()).sdp;
  pc2.close();
  const videoLine = sdp.split('m=').find((m) => m.startsWith('video')) || '';
  const offersVideo = /a=(sendrecv|sendonly)/.test(videoLine);
  return { offersVideo, hasCamera: devices.some((d) => d.kind === 'videoinput'), videoTracks: s.getVideoTracks().length,
    size: [c.width, c.height], center: px(c.width / 2, c.height / 2), corner: px(5, 5), swappedOnAdd, swappedOnReplace };
})()`;

const near = (rgb: number[], want: number[]): boolean => rgb.every((v, i) => Math.abs(v - want[i]) <= 40);

/** Launch a fresh headless Chromium with the init script for `avatar`, return PROBE's result
 *  (null = Chromium unavailable here → SKIP). */
async function probe(avatar: string | null, url: string): Promise<Record<string, any> | null> {
  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-vcam-'));
  let context: BrowserContext;
  let page;
  try {
    ({ context, page } = await launchPersistentBrowser({
      dataDir, headless: true,
      args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-file-for-fake-video-capture=/dev/null'],
    }));
  } catch (e) {
    console.log(`  ⚠️ SKIP — headless Chromium unavailable in this environment: ${(e as Error).message?.split('\n')[0]}`);
    return null;
  }
  try {
    await context.addInitScript(buildVirtualCameraInitScript(avatar));
    await page.goto(url);
    return await page.evaluate(PROBE) as Record<string, any>;
  } finally {
    await context.close().catch(() => {});
    rmSync(dataDir, { recursive: true, force: true });
  }
}

async function browserSide(): Promise<void> {
  const server = createServer((_q, r) => { r.setHeader('content-type', 'text/html'); r.end('<!doctype html><body></body>'); });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
  try {
    const avatar = await resolveAvatarDataUri('https://x/logo.svg', fakeFetch(BLUE_SVG, 'image/svg+xml'));
    const r = await probe(avatar, url);
    if (!r) return;
    check('enumerateDevices lists a camera', r.hasCamera === true);
    check('getUserMedia({video}) → one video track', r.videoTracks === 1, String(r.videoTracks));
    check('frame is 1280x720', r.size[0] === 1280 && r.size[1] === 720, String(r.size));
    check('center shows the avatar (Aimable blue)', near(r.center, [0, 76, 250]), String(r.center));
    check('corner is the light steel blue background', near(r.corner, [201, 212, 230]), String(r.corner));
    check('addTrack swaps outgoing video for the canvas', r.swappedOnAdd === true);
    check('replaceTrack swaps outgoing video for the canvas', r.swappedOnReplace === true);

    check('meet: no forced video line in an audio-only offer', r.offersVideo === false);
    const b = await probe(null, url);
    if (b) check('no avatar → blank light steel blue tile (not Chrome test pattern)', near(b.center, [201, 212, 230]), String(b.center));
  } finally {
    server.close();
  }
}

await nodeSide();
await browserSide();
if (failed) { console.error(`❌ ${failed} check(s) failed`); process.exit(1); }
console.log('✅ virtual-camera: all checks passed');
