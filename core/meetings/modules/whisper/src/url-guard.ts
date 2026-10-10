/**
 * url-guard — may this process send a request to a URL somebody else chose?
 *
 * The TypeScript twin of the Python outbound URL guard (`ssrf.py`, vendored into every Python image
 * that fetches a user-supplied URL). The two cannot share code, so they share a table instead: the
 * golden of `deploy/contracts/outbound-url.v1`. `outbound-url-vectors.json` beside this file is a
 * byte-identical copy (`scripts/parity.json`, fact `outbound-url-vectors`), as is the one the Python
 * tests read, and both test suites hold their guard to every row.
 *
 * The same three rules:
 *  1. Every address is checked as what a connection to it reaches. An IPv6 address carrying an IPv4
 *     one (mapped, compatible, translated, 6to4, Teredo, NAT64, ISATAP) is checked as that IPv4
 *     address too; a numeric host a resolver reads as IPv4 (`2130706433`, `0x7f.1`) is read so here.
 *  2. The refused ranges are explicit lists, not library flags.
 *  3. The connection is made to the checked address: `guardedLookup` resolves, checks every answer
 *     and hands the socket the address it checked (Node's `lookup` hook), so a record flipped after
 *     the check reaches nothing. Node builtins only.
 */
import { lookup as dnsLookup } from 'node:dns';
import { isIP } from 'node:net';

export type Resolver = (host: string) => Promise<string[]>;

export class SsrfError extends Error {
  constructor(message: string) { super(message); this.name = 'SsrfError'; }
}

const V4_BLOCKED: Array<[string, number]> = [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16],
  ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.0.2.0', 24], ['192.88.99.0', 24], ['192.168.0.0', 16],
  ['198.18.0.0', 15], ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
];
const V6_BLOCKED: Array<[string, number]> = [
  ['::', 128], ['::1', 128], ['64:ff9b:1::', 48], ['100::', 64], ['2001::', 23], ['2001:db8::', 32],
  ['3fff::', 20], ['5f00::', 16], ['fc00::', 7], ['fe80::', 10], ['fec0::', 10], ['ff00::', 8],
];
const BLOCKED_HOSTNAMES = new Set([
  'localhost', 'metadata', 'metadata.google.internal', 'metadata.goog', 'metadata.amazonaws.com',
  'instance-data', 'instance-data.ec2.internal',
]);
const ISATAP_IDS = new Set([0x00005efe, 0x01005efe, 0x02005efe, 0x03005efe]);
const NUMERIC_V4 = /^(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}$/;

type Addr = { v: 4; n: number } | { v: 6; n: bigint };

function dotted4(s: string): number | null {
  const parts = s.split('.');
  if (parts.length !== 4) return null;
  let n = 0;
  for (const p of parts) {
    if (!/^(0|[1-9][0-9]{0,2})$/.test(p) || Number(p) > 255) return null;
    n = n * 256 + Number(p);
  }
  return n;
}

/** inet_aton's reading of a numeric host: one to four parts, each decimal, octal (leading 0) or hex. */
function inetAton(s: string): number | null {
  if (!NUMERIC_V4.test(s)) return null;
  const vals: number[] = [];
  for (const p of s.split('.')) {
    let v: number;
    if (/^0x/.test(p)) v = p.length > 2 ? parseInt(p.slice(2), 16) : 0;
    else if (p.length > 1 && p.startsWith('0')) { if (!/^[0-7]+$/.test(p)) return null; v = parseInt(p, 8); }
    else v = parseInt(p, 10);
    if (!Number.isFinite(v)) return null;
    vals.push(v);
  }
  const last = vals[vals.length - 1];
  const head = vals.slice(0, -1);
  if (head.some((x) => x > 255)) return null;
  const room = 2 ** (8 * (4 - head.length));
  if (last >= room) return null;
  return head.reduce((acc, x, i) => acc + x * 2 ** (8 * (3 - i)), 0) + last;
}

function parse6(input: string): bigint | null {
  let s = input.split('%')[0];
  if (isIP(s) !== 6) return null;
  let tail: number[] = [];
  const lastColon = s.lastIndexOf(':');
  if (s.slice(lastColon + 1).includes('.')) {
    const v4 = dotted4(s.slice(lastColon + 1));
    if (v4 === null) return null;
    tail = [Math.floor(v4 / 65536), v4 % 65536];
    s = s.slice(0, lastColon + 1) + '0:0';
  }
  const halves = s.split('::');
  const left = halves[0] ? halves[0].split(':') : [];
  const right = halves.length > 1 && halves[1] ? halves[1].split(':') : [];
  const groups = halves.length > 1
    ? [...left, ...Array(8 - left.length - right.length).fill('0'), ...right]
    : left;
  if (groups.length !== 8) return null;
  if (tail.length) { groups[6] = tail[0].toString(16); groups[7] = tail[1].toString(16); }
  return groups.reduce((acc, g) => (acc << 16n) | BigInt(parseInt(g || '0', 16)), 0n);
}

/** `text` as an address, or null — including the numeric forms a resolver reads as IPv4. */
export function literalAddress(text: string): Addr | null {
  const t = (text || '').trim().replace(/^\[|\]$/g, '');
  const d = dotted4(t);
  if (d !== null) return { v: 4, n: d };
  if (t.includes(':')) { const n = parse6(t); return n === null ? null : { v: 6, n }; }
  const a = inetAton(t.toLowerCase());
  return a === null ? null : { v: 4, n: a };
}

const inNet4 = (n: number, [base, len]: [string, number]) =>
  len === 0 || Math.floor(n / 2 ** (32 - len)) === Math.floor((dotted4(base) as number) / 2 ** (32 - len));
const inNet6 = (n: bigint, [base, len]: [string, number]) =>
  (n >> BigInt(128 - len)) === ((parse6(base) as bigint) >> BigInt(128 - len));

/** Every IPv4 address an IPv6 address carries — what a socket dialled at it would reach. */
export function embeddedIpv4(n: bigint): number[] {
  const out: number[] = [];
  const low32 = Number(n & 0xffffffffn);
  const hi96 = n >> 32n;
  if (hi96 === 0xffffn) out.push(low32);                                   // mapped
  else if (hi96 === 0xffff0000n) out.push(low32);                          // translated
  else if (hi96 === 0n && n > 1n) out.push(low32);                         // compatible
  if (hi96 === 0x0064ff9b0000000000000000n) out.push(low32);               // NAT64 well-known
  if ((n >> 112n) === 0x2002n) out.push(Number((n >> 80n) & 0xffffffffn)); // 6to4
  if ((n >> 96n) === 0x20010000n) {                                         // Teredo (server, client)
    out.push(Number((n >> 64n) & 0xffffffffn), Number(~n & 0xffffffffn));
  }
  if (ISATAP_IDS.has(Number((n >> 32n) & 0xffffffffn))) out.push(low32);   // ISATAP
  return out;
}

/** True when a socket dialled at `addr` would reach an address this module refuses. Anything that is
 *  not an address is refused: unreadable is not the same as safe. */
export function isBlockedIp(addr: string | Addr): boolean {
  const a = typeof addr === 'string' ? literalAddress(addr) : addr;
  if (!a) return true;
  if (a.v === 4) return V4_BLOCKED.some((net) => inNet4(a.n, net));
  const carried = embeddedIpv4(a.n);
  if (carried.some((v4) => isBlockedIp({ v: 4, n: v4 }))) return true;
  if (V6_BLOCKED.some((net) => inNet6(a.n, net))) return true;
  return (a.n >> 120n) === 0n && carried.length === 0;
}

/** True for a name that can only name this deployment: localhost (and *.localhost), a cloud
 *  metadata name, or a single label. */
export function isBlockedHostname(hostname: string): boolean {
  const host = (hostname || '').trim().replace(/\.+$/, '').toLowerCase();
  if (!host) return true;
  if (BLOCKED_HOSTNAMES.has(host) || host.endsWith('.localhost')) return true;
  return !host.includes('.') && literalAddress(host) === null;
}

export const resolveHost: Resolver = (host) => new Promise((resolve) => {
  dnsLookup(host, { all: true }, (err, addresses) => {
    resolve(err ? [] : [...new Set(addresses.map((a) => a.address.split('%')[0]))]);
  });
});

/** The address(es) `host` is dialled at, every one checked — or SsrfError. */
export async function checkedAddresses(host: string, resolver: Resolver = resolveHost, what = 'URL'): Promise<string[]> {
  const refused = `${what} cannot target internal or private networks`;
  const literal = literalAddress(host);
  if (literal) {
    if (isBlockedIp(literal)) throw new SsrfError(refused);
    return [host];
  }
  if (isBlockedHostname(host)) throw new SsrfError(refused);
  const ips = await resolver(host);
  if (!ips.length) throw new SsrfError(`${what} hostname could not be resolved`);
  if (ips.some((ip) => isBlockedIp(ip))) throw new SsrfError(refused);
  return ips;
}

export interface CheckedUrl { url: URL; host: string; pinnedIps: string[] }

/** Check `url` (http(s), a host, every address it reaches); `resolve: false` checks it as written,
 *  for a value that is only stored. */
export async function validateUrl(raw: string, opts: { resolver?: Resolver; resolve?: boolean; what?: string } = {}): Promise<CheckedUrl> {
  const what = opts.what ?? 'URL';
  let url: URL;
  try { url = new URL((raw || '').trim()); } catch { throw new SsrfError(`${what} is not a valid URL`); }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new SsrfError(`${what} must use http or https scheme`);
  const host = url.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (!host) throw new SsrfError(`${what} must have a valid hostname`);
  if (opts.resolve === false) {
    const literal = literalAddress(host);
    if ((literal && isBlockedIp(literal)) || (!literal && isBlockedHostname(host))) {
      throw new SsrfError(`${what} cannot target internal or private networks`);
    }
    return { url, host, pinnedIps: literal ? [host] : [] };
  }
  return { url, host, pinnedIps: await checkedAddresses(host, opts.resolver, what) };
}

type LookupCallback = (err: NodeJS.ErrnoException | null, address: string | Array<{ address: string; family: number }>, family?: number) => void;

/** A `lookup` for `http.request`/`net.connect`: resolve at connect time, check every answer, and give
 *  the socket the first checked address — the connection is pinned to what was checked. */
export function guardedLookup(resolver: Resolver = resolveHost) {
  return (hostname: string, options: { all?: boolean } | number | undefined, callback: LookupCallback): void => {
    checkedAddresses(hostname, resolver).then(
      (ips) => {
        const all = ips.map((address) => ({ address, family: isIP(address) || 4 }));
        if (typeof options === 'object' && options?.all) callback(null, all);
        else callback(null, all[0].address, all[0].family);
      },
      (err: Error) => callback(Object.assign(err, { code: 'EVEXAREFUSED' }) as NodeJS.ErrnoException, ''),
    );
  };
}
