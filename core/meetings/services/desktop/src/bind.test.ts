/**
 * L3 — the desktop binds LOOPBACK by default (cards b7a91f2a52b8). /ingest has no auth and the
 * default canAccess (ownerOnly) is allow-all, so neither the gateway nor the ingest WS may be
 * reachable from the LAN unless the operator opts in (`host` / VEXA_DESKTOP_HOST).
 * Probe: a raw TCP connect to 127.0.0.1 (must open) and to a non-loopback IPv4 of this machine
 * (must be refused by default, must open with host '0.0.0.0'). The LAN probe is skipped,
 * with a line saying so, when the machine has no non-loopback IPv4.
 * Run: npx tsx src/bind.test.ts
 */
import * as net from 'node:net';
import { networkInterfaces } from 'node:os';
import { startDesktop } from './desktop.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };

const tcpOpens = (host: string, port: number) => new Promise<boolean>((res) => {
  const s = net.connect({ host, port });
  const done = (ok: boolean) => { s.destroy(); res(ok); };
  s.setTimeout(1500, () => done(false));
  s.on('connect', () => done(true));
  s.on('error', () => done(false));
});

const lanIp = Object.values(networkInterfaces()).flat()
  .find((a) => a && a.family === 'IPv4' && !a.internal)?.address;

async function main() {
  delete process.env.VEXA_DESKTOP_HOST;

  // 1) default → loopback only
  let desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true });
  try {
    check('default host is 127.0.0.1', desk.host === '127.0.0.1', String(desk.host));
    check('default: gateway open on 127.0.0.1', await tcpOpens('127.0.0.1', desk.gatewayPort));
    check('default: ingest open on 127.0.0.1', await tcpOpens('127.0.0.1', desk.ingestPort));
    if (lanIp) {
      check(`default: gateway REFUSED on LAN ${lanIp}`, !(await tcpOpens(lanIp, desk.gatewayPort)), 'gateway reachable from the LAN');
      check(`default: ingest REFUSED on LAN ${lanIp}`, !(await tcpOpens(lanIp, desk.ingestPort)), 'ingest reachable from the LAN');
    } else console.log('  ⏭  no non-loopback IPv4: LAN refusal probe skipped');
  } finally { await desk.close(); }

  // 2) explicit opt-in host '0.0.0.0' → all interfaces
  desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, host: '0.0.0.0' });
  try {
    check("host '0.0.0.0' is reported", desk.host === '0.0.0.0', String(desk.host));
    if (lanIp) {
      check(`0.0.0.0: gateway open on LAN ${lanIp}`, await tcpOpens(lanIp, desk.gatewayPort));
      check(`0.0.0.0: ingest open on LAN ${lanIp}`, await tcpOpens(lanIp, desk.ingestPort));
    }
  } finally { await desk.close(); }

  // 3) VEXA_DESKTOP_HOST is the default; opts.host wins over it
  process.env.VEXA_DESKTOP_HOST = '0.0.0.0';
  try {
    desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true });
    check('VEXA_DESKTOP_HOST sets the host', desk.host === '0.0.0.0', String(desk.host));
    await desk.close();
    desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, host: '127.0.0.1' });
    check('opts.host overrides VEXA_DESKTOP_HOST', desk.host === '127.0.0.1', String(desk.host));
    if (lanIp) check(`opts.host 127.0.0.1: gateway REFUSED on LAN ${lanIp}`, !(await tcpOpens(lanIp, desk.gatewayPort)));
    await desk.close();
  } finally { delete process.env.VEXA_DESKTOP_HOST; }

  if (failed) { console.error(`\n❌ bind (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ bind (L3): gateway + ingest bind 127.0.0.1 by default; LAN exposure only via host / VEXA_DESKTOP_HOST.');
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
