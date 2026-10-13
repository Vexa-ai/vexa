/**
 * Fetch an image the control plane accepted, as a data URI the page can draw.
 *
 * The BOT fetches it, not the page: a meeting page's own CSP blocks most third-party images, and the
 * URL has to be re-checked at FETCH time — the control plane validated it seconds or minutes earlier
 * and a redirect can still lead somewhere else. Only http(s) URLs and raster image data URIs, only
 * the types a canvas can draw, size-capped, each redirect hop re-checked, and any hop that lands on a
 * private address refused.
 *
 * Node-only (fetch + Buffer): no DOM, no workspace imports.
 */

export const DRAWABLE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const;
export const DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_REDIRECTS = 5;

export class ImageFetchRefused extends Error {
  constructor(message: string) { super(message); this.name = 'ImageFetchRefused'; }
}

export interface FetchImageOptions {
  maxBytes?: number;
  timeoutMs?: number;
  /** Injected in tests; production uses the global fetch. */
  fetchImpl?: typeof fetch;
}

/** True for hosts a bot must never fetch from: loopback, link-local, private ranges, cloud metadata. */
export function isPrivateHost(host: string): boolean {
  const name = host.trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!name) return true;
  if (name === 'localhost' || name.endsWith('.localhost') || name.endsWith('.local')) return true;
  if (name.endsWith('.internal') || name === 'metadata.google.internal') return true;
  const v4 = name.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [a, b] = v4.slice(1).map(Number);
    if ([a, b].some((n) => n > 255)) return true;
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254)
      || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a >= 224;
  }
  if (name.includes(':')) {
    if (name === '::' || name === '::1') return true;
    const mapped = name.match(/^::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/);
    if (mapped) return isPrivateHost(mapped[1]);
    const head = name.split(':')[0];
    return /^(fc|fd|fe8|fe9|fea|feb|ff)/.test(head);   // unique-local, link-local, multicast
  }
  return false;   // a public name; its address is the deployment's DNS answer at connect time
}

function dataUriImage(src: string, maxBytes: number): string {
  const match = /^data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)$/.exec(src);
  if (!match) throw new ImageFetchRefused('only png/jpeg/gif/webp base64 data URIs can be drawn');
  const bytes = Buffer.from(match[2], 'base64');
  if (bytes.length === 0) throw new ImageFetchRefused('the image data URI is empty');
  if (bytes.length > maxBytes) throw new ImageFetchRefused(`the image is larger than ${maxBytes} bytes`);
  return src;
}

/** `src` (http(s) URL or image data URI) as a `data:` URI, or `ImageFetchRefused`. */
export async function fetchImageAsDataUri(src: string, opts: FetchImageOptions = {}): Promise<string> {
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_IMAGE_BYTES;
  if (src.startsWith('data:')) return dataUriImage(src, maxBytes);

  const doFetch = opts.fetchImpl ?? fetch;
  let url: URL;
  try { url = new URL(src); } catch { throw new ImageFetchRefused('the image URL does not parse'); }

  for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
    if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new ImageFetchRefused('the image URL must be http or https');
    if (isPrivateHost(url.hostname)) throw new ImageFetchRefused('the image URL points at a private address');

    const response = await doFetch(url, {
      redirect: 'manual',                                  // every hop is re-checked, never followed blind
      signal: AbortSignal.timeout(opts.timeoutMs ?? 10_000),
    });
    const location = response.headers.get('location');
    if (response.status >= 300 && response.status < 400 && location) {
      url = new URL(location, url);
      continue;
    }
    if (!response.ok) throw new ImageFetchRefused(`the image could not be fetched (HTTP ${response.status})`);

    const type = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    if (!(DRAWABLE_IMAGE_TYPES as readonly string[]).includes(type)) {
      throw new ImageFetchRefused(`the image is ${type || 'of no declared type'}; png/jpeg/gif/webp can be drawn`);
    }
    const declared = Number(response.headers.get('content-length'));
    if (Number.isFinite(declared) && declared > maxBytes) throw new ImageFetchRefused(`the image is larger than ${maxBytes} bytes`);

    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length === 0) throw new ImageFetchRefused('the image is empty');
    if (bytes.length > maxBytes) throw new ImageFetchRefused(`the image is larger than ${maxBytes} bytes`);
    return `data:${type};base64,${bytes.toString('base64')}`;
  }
  throw new ImageFetchRefused(`the image URL redirected more than ${MAX_REDIRECTS} times`);
}
