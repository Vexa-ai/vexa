import { build } from 'esbuild';
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';

const outdir = 'dist';
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

await build({
  entryPoints: {
    background: 'src/background.ts',
    content: 'src/content.ts',
    inpage: 'src/inpage.ts',
    sidepanel: 'src/sidepanel.ts',
    offscreen: 'src/offscreen.ts',
    'mic-permission': 'src/mic-permission.ts',
  },
  outdir,
  bundle: true,
  format: 'iife',
  target: 'es2020',
  logLevel: 'info',
});

cpSync('src/sidepanel.html', `${outdir}/sidepanel.html`);
cpSync('src/offscreen.html', `${outdir}/offscreen.html`);
cpSync('src/mic-permission.html', `${outdir}/mic-permission.html`);
cpSync('assets', `${outdir}/assets`, { recursive: true });

const now = new Date();
const pad = (n) => String(n).padStart(2, '0');
const stampHuman = `${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
writeFileSync(`${outdir}/build-stamp.txt`, JSON.stringify({ ts: Date.now(), human: stampHuman }));

// Version the dist manifest per build so chrome://extensions shows which build
// is loaded (each dotted part must be <= 65535): 0.<MDD>.<HMM>.<SS>
const manifest = JSON.parse(readFileSync('manifest.json', 'utf8'));
manifest.version = `0.${now.getMonth() + 1}${pad(now.getDate())}.${now.getHours()}${pad(now.getMinutes())}.${now.getSeconds()}`;
manifest.version_name = `dev ${stampHuman}`;
writeFileSync(`${outdir}/manifest.json`, JSON.stringify(manifest, null, 2));

console.log(`Build stamp: ${stampHuman} (manifest version ${manifest.version})`);

console.log('Built vexa-extension → dist/. Load that folder as an unpacked extension.');
