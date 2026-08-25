const browserBaseUrl = () => globalThis.location?.href || 'http://127.0.0.1/';

/**
 * Resolve a fixture resource without allowing executable URL schemes.
 * The evaluator intentionally supports another localhost origin for its replay backend, so origin
 * equality is not the boundary; the protocol is. Credentials are never valid fixture input.
 */
export function safeResourceUrl(value, { allowBlob = false } = {}) {
  const raw = String(value ?? '');
  if (!raw || raw.length > 4096) throw new Error('fixture resource URL is missing or too long');
  const resolved = new URL(raw, browserBaseUrl());
  const allowedProtocol = resolved.protocol === 'http:'
    || resolved.protocol === 'https:'
    || (allowBlob && resolved.protocol === 'blob:');
  if (!allowedProtocol || resolved.username || resolved.password) {
    throw new Error(`fixture resource URL is not allowed: ${resolved.protocol}`);
  }
  return resolved.href;
}
