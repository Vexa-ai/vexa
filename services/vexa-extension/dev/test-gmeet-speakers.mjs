import { createRequire } from 'node:module';
import { readdirSync, existsSync } from 'node:fs';
const require = createRequire(import.meta.url);
const { chromium } = require('../../vexa-bot/core/node_modules/playwright');

const home = process.env.HOME;
const chrome = readdirSync(`${home}/.cache/ms-playwright`).filter(d => d.startsWith('chromium-') && !d.includes('headless'))
  .map(d => `${home}/.cache/ms-playwright/${d}/chrome-linux/chrome`).find(existsSync);

const browser = await chromium.launch({ headless: true, executablePath: chrome });
const page = await browser.newPage();
await page.goto('about:blank');
await page.addScriptTag({ path: new URL('../../vexa-bot/core/dist/browser-utils.global.js', import.meta.url).pathname });

const results = await page.evaluate(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = {};
  document.body.innerHTML = `
    <div data-participant-id="A"><span class="notranslate">Alice Aronson</span><div class="ind"></div></div>
    <div data-participant-id="B"><span class="notranslate">Bob Barker</span><div class="ind"></div></div>
    <div data-participant-id="S" data-self-name="Me Myself"><span class="notranslate">Me Myself</span><div class="ind"></div></div>`;
  const tile = (id) => document.querySelector(`[data-participant-id="${id}"]`);

  const named = [];
  const sp = window.VexaBrowserUtils.createGmeetSpeakers({
    selfName: 'Me Myself', pollMs: 60, audioWindowMs: 300,
    learnAfterSilentMs: 400, learnMinScore: 2,
    onName: (i, n) => named.push([i, n]),
    log: () => {},
  });

  // T1: Alice speaking (known class) + audio on track 0 → lock 0=Alice
  tile('A').classList.add('speaking');
  for (let k = 0; k < 6; k++) { sp.reportTrackAudio(0); await sleep(70); }
  tile('A').classList.remove('speaking');
  out.t1 = sp.resolve(0);

  // T2: Bob speaking + audio on track 1 → lock 1=Bob (Alice taken)
  tile('B').classList.add('speaking');
  for (let k = 0; k < 6; k++) { sp.reportTrackAudio(1); await sleep(70); }
  tile('B').classList.remove('speaking');
  out.t2 = sp.resolve(1);

  // T3: self tile speaking + audio on track 2 → must stay unmapped
  tile('S').classList.add('speaking');
  for (let k = 0; k < 5; k++) { sp.reportTrackAudio(2); await sleep(70); }
  tile('S').classList.remove('speaking');
  out.t3 = sp.resolve(2);
  out.onName = named;
  sp.destroy();

  // T4: selector rot — fresh module, indicator is an UNKNOWN class 'XqZwQQ'
  document.body.innerHTML = `
    <div data-participant-id="C"><span class="notranslate">Carol Chen</span></div>`;
  const sp2 = window.VexaBrowserUtils.createGmeetSpeakers({
    selfName: 'Me Myself', pollMs: 60, audioWindowMs: 300,
    learnAfterSilentMs: 250, learnMinScore: 2, log: () => {},
  });
  const c = tile('C');
  await sleep(400); // let known classes go "silent"
  for (let k = 0; k < 5; k++) {
    c.classList.add('XqZwQQ');           // unknown indicator toggles on
    await sleep(30);
    sp2.reportTrackAudio(3);             // audio correlates
    await sleep(120);
    c.classList.remove('XqZwQQ');
    await sleep(60);
  }
  // now the learned class should light Carol up while she "speaks"
  c.classList.add('XqZwQQ');
  for (let k = 0; k < 6; k++) { sp2.reportTrackAudio(3); await sleep(70); }
  const st = sp2.getState();
  out.t4 = { learned: st.selectorStats.learnedClasses, resolve: sp2.resolve(3) };
  sp2.destroy();
  return out;
});

console.log(JSON.stringify(results, null, 1));
const ok = results.t1.name === 'Alice Aronson' && results.t1.locked
  && results.t2.name === 'Bob Barker' && results.t2.locked
  && results.t3.name === null
  && results.t4.learned.length > 0 && results.t4.resolve.name === 'Carol Chen';
console.log(ok ? 'ALL TESTS PASSED' : 'TESTS FAILED');
await browser.close();
process.exit(ok ? 0 : 1);
