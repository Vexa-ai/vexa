/**
 * browser — the launch keeps exactly one page and blanks it: a restored profile that reopens an old
 * meeting's join URL leaves one page on about:blank, and the log names hosts only. A stand-in
 * context; no Chromium. Run: npx tsx src/browser-launch.test.ts
 */
import { keepOneBlankPage, pageHost } from './browser';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

function standIn(urls: string[], opts: { closeThrows?: boolean; gotoThrows?: boolean } = {}) {
  const open: any[] = [];
  const make = (u: string) => {
    const p: any = {
      u,
      url: () => p.u,
      goto: async (to: string) => { if (opts.gotoThrows) throw new Error(`goto failed ${to}`); p.u = to; },
      close: async () => { if (opts.closeThrows) throw new Error('close failed'); open.splice(open.indexOf(p), 1); },
    };
    open.push(p);
    return p;
  };
  urls.forEach(make);
  return { open, context: { pages: () => [...open], newPage: async () => make('about:blank') } as any };
}

async function main() {
  const SECRET = 'pwd=s3cr3t';
  const old = `https://us05web.zoom.us/wc/123456789/join?${SECRET}&fromPWA=1`;

  // a restored tab of an old meeting next to the start page → one page, at about:blank, hosts only
  let lines: string[] = [];
  let s = standIn([old, 'about:blank']);
  let page = await keepOneBlankPage(s.context, (l) => lines.push(l));
  check(s.open.length === 1, `one page left; got ${s.open.length}`);
  check(page === s.open[0] && page.url() === 'about:blank', `the page left is at about:blank; got ${page.url()}`);
  check(lines.length === 1 && lines[0].includes('2 page(s)') && lines[0].includes('us05web.zoom.us') && lines[0].includes('about:blank'),
    `one log line with the count and hosts; got ${JSON.stringify(lines)}`);
  check(!lines.join('\n').includes('/wc/') && !lines.join('\n').includes(SECRET) && !lines.join('\n').includes('123456789'),
    `the log carries no path or query; got ${JSON.stringify(lines)}`);

  // no page at all → a new one
  lines = [];
  s = standIn([]);
  page = await keepOneBlankPage(s.context, (l) => lines.push(l));
  check(s.open.length === 1 && page.url() === 'about:blank', 'no page ⇒ one new blank page');

  // close and goto failing are logged, never thrown
  lines = [];
  s = standIn([old, old], { closeThrows: true, gotoThrows: true });
  let threw = false;
  try { await keepOneBlankPage(s.context, (l) => lines.push(l)); } catch { threw = true; }
  check(!threw, 'a failed close / goto never throws');
  check(lines.some((l) => l.includes('could not close')) && lines.some((l) => l.includes('could not blank')),
    `failures are logged; got ${JSON.stringify(lines)}`);
  check(!lines.join('\n').includes(SECRET) && !lines.join('\n').includes('/wc/'), 'failure lines carry no URL');

  check(pageHost('') === '(empty)' && pageHost('about:blank') === 'about:blank' && pageHost('https://a.zoom.us/x?y=1') === 'a.zoom.us',
    'pageHost: host only, about:blank and empty as they are');

  if (fails.length) { console.log('❌ FAIL —\n  ' + fails.join('\n  ')); process.exit(1); }
  console.log('✅ browser-launch.test — one page, blanked, hosts only in the log');
}
main().catch((e) => { console.error(e); process.exit(1); });
