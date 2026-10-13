/**
 * media-fetch — what the bot will draw on its camera. The control plane already accepted the URL;
 * this is the fetch-time re-check: http(s) only, drawable raster types only, size-capped, and every
 * redirect hop judged rather than followed blind. Offline: the transport is injected.
 * Run: npx tsx src/media-fetch.test.ts
 */
import { DEFAULT_MAX_IMAGE_BYTES, ImageFetchRefused, fetchImageAsDataUri, isPrivateHost } from './media-fetch.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};
const refuses = async (name: string, fn: () => Promise<unknown>, expect: string) => {
  try { await fn(); check(name, false, 'it was accepted'); }
  catch (e) {
    const msg = String((e as Error).message);
    check(name, e instanceof ImageFetchRefused && msg.includes(expect), msg);
  }
};

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');
const reply = (body: Buffer, headers: Record<string, string>, status = 200) =>
  new Response(body, { status, headers });

// A transport that answers a scripted route table and records what it was asked for.
const asked: string[] = [];
const transport = (routes: Record<string, Response | (() => Response)>): typeof fetch =>
  (async (input: any) => {
    const url = String(input);
    asked.push(url);
    const hit = routes[url];
    if (!hit) throw new Error(`unexpected fetch: ${url}`);
    return typeof hit === 'function' ? hit() : hit;
  }) as unknown as typeof fetch;

async function main() {
  // ── which hosts a bot may fetch from ──
  for (const host of ['localhost', '127.0.0.1', '10.1.2.3', '172.20.0.5', '192.168.1.4', '169.254.169.254',
                      '0.0.0.0', '[::1]', '::ffff:127.0.0.1', 'redis.internal', 'printer.local', '224.0.0.1']) {
    check(`private host refused: ${host}`, isPrivateHost(host));
  }
  for (const host of ['picsum.photos', '93.184.216.34', 'cdn.example.com', '2606:4700::1111']) {
    check(`public host allowed: ${host}`, !isPrivateHost(host));
  }

  // ── the happy path ──
  const ok = await fetchImageAsDataUri('https://cdn.example.com/a.png', {
    fetchImpl: transport({ 'https://cdn.example.com/a.png': reply(PNG, { 'content-type': 'image/png' }) }),
  });
  check('a public png comes back as a data URI', ok === `data:image/png;base64,${PNG.toString('base64')}`, ok.slice(0, 40));

  // ── redirects are judged hop by hop ──
  asked.length = 0;
  const redirected = await fetchImageAsDataUri('https://cdn.example.com/r', {
    fetchImpl: transport({
      'https://cdn.example.com/r': reply(Buffer.alloc(0), { location: 'https://img.example.net/final.jpg' }, 302),
      'https://img.example.net/final.jpg': reply(PNG, { 'content-type': 'image/jpeg' }),
    }),
  });
  check('a redirect to another public host is followed', redirected.startsWith('data:image/jpeg;base64,'));
  check('each hop was fetched in turn', asked.length === 2, JSON.stringify(asked));

  await refuses('a redirect to a private address is refused', () => fetchImageAsDataUri('https://cdn.example.com/r', {
    fetchImpl: transport({ 'https://cdn.example.com/r': reply(Buffer.alloc(0), { location: 'http://169.254.169.254/latest' }, 302) }),
  }), 'private address');

  await refuses('a redirect loop ends', () => fetchImageAsDataUri('https://cdn.example.com/loop', {
    fetchImpl: transport({ 'https://cdn.example.com/loop': () => reply(Buffer.alloc(0), { location: 'https://cdn.example.com/loop' }, 302) }),
  }), 'redirected more than');

  // ── what cannot be drawn ──
  await refuses('a private address is refused up front', () => fetchImageAsDataUri('http://127.0.0.1/a.png', { fetchImpl: transport({}) }), 'private address');
  await refuses('a non-http scheme is refused', () => fetchImageAsDataUri('ftp://cdn.example.com/a.png', { fetchImpl: transport({}) }), 'http or https');
  await refuses('an unparseable URL is refused', () => fetchImageAsDataUri('notaurl', { fetchImpl: transport({}) }), 'does not parse');
  await refuses('html is not an image', () => fetchImageAsDataUri('https://cdn.example.com/page', {
    fetchImpl: transport({ 'https://cdn.example.com/page': reply(Buffer.from('<html>'), { 'content-type': 'text/html' }) }),
  }), 'png/jpeg/gif/webp');
  await refuses('an svg is not drawable', () => fetchImageAsDataUri('https://cdn.example.com/a.svg', {
    fetchImpl: transport({ 'https://cdn.example.com/a.svg': reply(Buffer.from('<svg/>'), { 'content-type': 'image/svg+xml' }) }),
  }), 'png/jpeg/gif/webp');
  await refuses('a 404 is refused', () => fetchImageAsDataUri('https://cdn.example.com/missing.png', {
    fetchImpl: transport({ 'https://cdn.example.com/missing.png': reply(Buffer.alloc(0), { 'content-type': 'image/png' }, 404) }),
  }), 'HTTP 404');
  await refuses('an empty body is refused', () => fetchImageAsDataUri('https://cdn.example.com/empty.png', {
    fetchImpl: transport({ 'https://cdn.example.com/empty.png': reply(Buffer.alloc(0), { 'content-type': 'image/png' }) }),
  }), 'empty');
  await refuses('a declared over-size image is refused before it is read', () => fetchImageAsDataUri('https://cdn.example.com/big.png', {
    fetchImpl: transport({ 'https://cdn.example.com/big.png': reply(PNG, { 'content-type': 'image/png', 'content-length': String(DEFAULT_MAX_IMAGE_BYTES + 1) }) }),
  }), 'larger than');
  await refuses('a body over the cap is refused after it is read', () => fetchImageAsDataUri('https://cdn.example.com/big2.png', {
    maxBytes: 8,
    fetchImpl: transport({ 'https://cdn.example.com/big2.png': reply(Buffer.alloc(64, 1), { 'content-type': 'image/png' }) }),
  }), 'larger than');

  // ── data URIs the control plane already accepted ──
  const inline = `data:image/png;base64,${PNG.toString('base64')}`;
  check('an image data URI passes through untouched', (await fetchImageAsDataUri(inline)) === inline);
  await refuses('an svg data URI is refused', () => fetchImageAsDataUri('data:image/svg+xml;base64,PHN2Zy8+'), 'data URIs can be drawn');
  await refuses('an over-size data URI is refused', () => fetchImageAsDataUri(`data:image/png;base64,${Buffer.alloc(64, 1).toString('base64')}`, { maxBytes: 8 }), 'larger than');

  if (failed) { console.error(`\n❌ media-fetch: ${failed} check(s) failed`); process.exit(1); }
  console.log('\n✅ media-fetch: all checks passed');
}

main();
